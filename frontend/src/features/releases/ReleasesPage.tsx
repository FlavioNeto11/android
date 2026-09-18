import { FolderInput, Package, ShieldCheck, Smartphone } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import type { AppRelease, DeviceAppState } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { toast, toastError } from '../../store/toasts';
import styles from './Releases.module.css';

const ESTADO_TOM: Record<string, 'success' | 'warning' | 'danger' | 'neutral'> = {
  installable: 'success', validated: 'success', ready: 'success',
  inspected: 'neutral', imported: 'neutral', installed: 'neutral', verifying: 'neutral', installing: 'neutral',
  invalid: 'danger', incompatible: 'danger', install_failed: 'danger', verify_failed: 'danger',
  version_drift: 'warning', missing: 'warning',
};

function tom(estado: string): 'success' | 'warning' | 'danger' | 'neutral' {
  return ESTADO_TOM[estado] ?? 'neutral';
}

/**
 * Aplicativos: o que foi importado da pasta `apks/inbox`, o que está instalado em cada aparelho e qual assinatura
 * foi aprovada. O sistema nunca baixa APK sozinho — os arquivos são colocados na pasta por uma pessoa.
 */
export function ReleasesPage() {
  const [releases, setReleases] = useState<AppRelease[] | null>(null);
  const [estados, setEstados] = useState<DeviceAppState[]>([]);
  const [importando, setImportando] = useState(false);
  const token = useRef(0);

  const carregar = useCallback(async () => {
    const meu = ++token.current;
    const [r, e] = await Promise.allSettled([api.listReleases(), api.listAppState()]);
    if (meu !== token.current) return;
    if (r.status === 'fulfilled') setReleases(r.value);
    else {
      setReleases([]);
      toastError('Não foi possível listar os aplicativos', r.reason);
    }
    if (e.status === 'fulfilled') setEstados(e.value);
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
                </dl>
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
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
