import {
  DownloadCloud, ExternalLink, FlaskConical, FolderInput, Package, Power, PowerOff, RefreshCw, Send, ShieldCheck,
  ShieldX, Smartphone, Store, TrendingUp, Undo2, Zap,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import type {
  AppCatalogEntry, AppRelease, DeviceAppState, DistributeDevice, Instance, ReleaseChannel, StoreStatus,
} from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import type { Tone } from '../../lib/status';
import { selectStoreInstance, useAppStore } from '../../store/app';
import { chaveDoApp } from '../../store/reducer';
import { runInstanceAction } from '../devices/actions';
import { toast, toastError } from '../../store/toasts';
import styles from './Releases.module.css';

/** O que aconteceu com cada aparelho ao distribuir. `incompatible` não é "ainda não": é "nunca, e por isto". */
const ENTREGA_TOM: Record<string, Tone> = {
  started: 'info', already: 'success', pending: 'neutral', incompatible: 'warning',
};
const ENTREGA_ROTULO: Record<string, string> = {
  started: 'instalando', already: 'já tem', pending: 'pendente', incompatible: 'não roda aqui',
};

const ESTADO_TOM: Record<string, Tone> = {
  installable: 'success', validated: 'success', ready: 'success',
  inspected: 'neutral', imported: 'neutral', installed: 'neutral', verifying: 'neutral', installing: 'neutral',
  invalid: 'danger', incompatible: 'danger', install_failed: 'danger', verify_failed: 'danger',
  version_drift: 'warning', missing: 'warning',
};

/** Idade da ÚLTIMA leitura do aparelho. "Verificado" sem data é o pior caso: a afirmação foi herdada de uma
 *  instalação e nunca mais conferida — foi assim que dois aparelhos exibiram "pronto" por três dias, escrito
 *  quando aqueles ids eram outros aparelhos físicos. */
export function idadeDaLeitura(verifiedAt: string | null | undefined): { rotulo: string; tom: Tone } {
  if (!verifiedAt) return { rotulo: 'nunca verificado no aparelho', tom: 'warning' };
  const ms = Date.now() - new Date(verifiedAt).getTime();
  if (!Number.isFinite(ms) || ms < 0) return { rotulo: 'verificado', tom: 'neutral' };
  const horas = ms / 3_600_000;
  if (horas < 1) return { rotulo: 'lido há minutos', tom: 'success' };
  if (horas < 24) return { rotulo: `lido há ${Math.floor(horas)} h`, tom: 'success' };
  const dias = Math.floor(horas / 24);
  return { rotulo: `lido há ${dias} dia${dias > 1 ? 's' : ''}`, tom: 'warning' };
}

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
  const [loja, setLoja] = useState<StoreStatus | null>(null);
  const [entregas, setEntregas] = useState<Record<string, DistributeDevice[]>>({});
  const [buscando, setBuscando] = useState(false);
  const [alvo, setAlvo] = useState<Record<string, string>>({});
  // Qual APLICATIVO a loja está operando. Antes não havia escolha: o backend caía no Instagram por omissão e,
  // pelo painel, a loja só sabia buscar o Instagram — o caminho da loja para um segundo app não existia.
  const [apps, setApps] = useState<AppCatalogEntry[]>([]);
  const [pacoteDaLoja, setPacoteDaLoja] = useState<string | null>(null);
  // Estado AO VIVO da loja (eventos do painel); o da rota `/store` é só a foto do último carregamento.
  const lojaViva = useAppStore((s) => selectStoreInstance(s));
  const [importando, setImportando] = useState(false);
  const token = useRef(0);

  const carregar = useCallback(async () => {
    const meu = ++token.current;
    const [r, e, i, a] = await Promise.allSettled([
      api.listReleases(), api.listAppState(), api.listInstances(), api.listAppCatalog()]);
    if (meu !== token.current) return;
    if (r.status === 'fulfilled') setReleases(r.value);
    else {
      setReleases([]);
      toastError('Não foi possível listar os aplicativos', r.reason);
    }
    // As outras três cargas eram engolidas: a tela dizia "Nenhum aplicativo catalogado nos aparelhos ainda" e
    // "nenhum aparelho" quando o problema era a requisição ter falhado.
    if (e.status === 'fulfilled') setEstados(e.value);
    else toastError('Não foi possível ler o que está instalado nos aparelhos', e.reason);
    // A loja é FONTE do aplicativo, nunca destino: fora da lista de aparelhos de prova.
    if (i.status === 'fulfilled') setAparelhos(i.value.filter((x) => x.kind !== 'store'));
    else toastError('Não foi possível listar os aparelhos', i.reason);
    if (a.status === 'fulfilled') {
      setApps(a.value);
      setPacoteDaLoja((atual) => atual ?? a.value.find((x) => x.has_catalog)?.package ?? a.value[0]?.package ?? null);
    } else toastError('Não foi possível listar os aplicativos conhecidos', a.reason);
  }, []);

  // O estado da loja depende do APP escolhido: trocar o app no seletor refaz a pergunta.
  const carregarLoja = useCallback(async (pkg: string | null) => {
    if (!pkg) return;
    try {
      setLoja(await api.storeStatus(pkg));
    } catch (e) {
      setLoja(null);
      toastError('Não foi possível ler o estado da loja', e);
    }
  }, []);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  useEffect(() => {
    void carregarLoja(pacoteDaLoja);
  }, [carregarLoja, pacoteDaLoja]);

  // AO VIVO: cada mudança de estado de app por aparelho chega como evento (`app_state.updated`) e entra na
  // lista sem ninguém recarregar. É o que torna verdadeira a frase "o resultado aparece aqui".
  const estadosAoVivo = useAppStore((s) => s.appState);
  const estadosMostrados = estados
    .map((e) => estadosAoVivo[chaveDoApp(e.instance_id, e.package_name)] ?? e)
    .concat(Object.values(estadosAoVivo).filter(
      (v) => !estados.some((e) => e.instance_id === v.instance_id && e.package_name === v.package_name)));

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
      note: { label: 'Observação (de onde veio o arquivo)', placeholder: 'ex.: copiado da loja em 18/09 — sem dados de conta' },
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

  async function verificar(e: DeviceAppState) {
    try {
      const r = await api.verifyApp(e.instance_id, e.package_name);
      toast({
        tone: 'info', title: `Relendo ${e.package_name} no ${e.instance_id}`,
        message: `Comando ${r.command_id}: o resultado e a nova data de verificação aparecem aqui sozinhos.`,
      });
    } catch (err) {
      toastError('Não foi possível verificar o aplicativo neste aparelho', err);
    }
  }

  // ------------------------------------------------------------------ a loja como fonte
  const estadoDaLoja = lojaViva?.state ?? loja?.state ?? null;
  const lojaLigada = estadoDaLoja === 'online';

  async function abrirNaLoja() {
    if (!pacoteDaLoja) return;
    try {
      await api.storeOpenListing(pacoteDaLoja);
      toast({
        tone: 'info', title: 'Página aberta na Play Store da loja',
        message: 'Instalar ou atualizar é um toque SEU, na janela do emulador — o sistema nunca toca nesse botão.',
      });
    } catch (e) {
      toastError('Não foi possível abrir a Play Store', e);
    }
  }

  async function buscarDaLoja() {
    if (!pacoteDaLoja) return;
    setBuscando(true);
    try {
      const r = await api.storeSync(pacoteDaLoja);
      toast({
        tone: 'info', title: `Buscando ${pacoteDaLoja} na loja…`,
        message: `Comando ${r.command_id}: a cópia vem do aparelho-loja por adb; nada é baixado da rede. `
          + 'A versão aparece aqui ao terminar.',
      });
      // 202: a cópia roda no aparelho. Recarrega algumas vezes até o catálogo parar de mudar.
      for (const espera of [3000, 6000, 12000]) {
        await new Promise((ok) => setTimeout(ok, espera));
        await carregar();
      }
    } catch (e) {
      toastError('Não foi possível buscar da loja', e);
    } finally {
      setBuscando(false);
    }
  }

  async function distribuir(r: AppRelease, agora: boolean) {
    if (agora) {
      const { confirmed } = await confirm({
        title: `Instalar ${r.version_name} em todos agora?`,
        // A frase tem de ser a do código: o rodízio só liga aparelho DESTA máquina (`_rotate` exclui os
        // externos). Prometer que ele ligaria os de outro servidor era afirmar o que não acontece (#44/#84).
        body: 'O rodízio vai ligar os aparelhos desligados DESTA máquina, um grupo por vez dentro das vagas, '
          + 'instalar e ceder a vaga ao próximo. Aparelho de outro servidor que esteja desligado não é ligado '
          + 'daqui: ele fica marcado e instala quando voltar. Aparelho com tarefa em andamento não é '
          + 'interrompido: recebe antes da próxima. Sem isto, cada aparelho recebe a versão quando pegar a '
          + 'próxima tarefa.',
        confirmLabel: 'Instalar em todos agora',
      });
      if (!confirmed) return;
    }
    try {
      const resposta = await api.releaseLifecycle(r.id, { verb: 'distribute', eager: agora });
      setEntregas((e) => ({ ...e, [r.id]: resposta.devices ?? [] }));
      const iniciados = (resposta.devices ?? []).filter((d) => d.outcome === 'started').length;
      toast({
        // Aceito não é concluído: a instalação leva minutos e cada aparelho tem seu próprio desfecho.
        tone: 'info', title: agora ? 'Entrega imediata pedida' : 'Versão distribuída',
        message: `${iniciados} aparelho(s) instalando agora; os demais aparecem abaixo com o motivo. `
          + 'O desfecho de cada um chega sozinho na lista “O que está instalado”.',
      });
      await carregar();
    } catch (e) {
      toastError('O pedido foi recusado', e);
    }
  }

  async function pedir(releaseId: string, body: Parameters<typeof api.releaseLifecycle>[1], titulo: string) {
    try {
      const r = await api.releaseLifecycle(releaseId, body);
      await carregar();
      // `promote`/`quarantine` são decisões de banco: decidiram AGORA, e verde é honesto. Canário, rollback e
      // instalação mexem no aparelho e voltam apenas ACEITOS — verde ali chamava de sucesso o que só tinha sido
      // aceito. O desfecho chega pelo comando e por `app_state.updated`, e a lista abaixo se atualiza sozinha.
      const noAparelho = Boolean(body.instance_id);
      toast({
        tone: noAparelho ? 'info' : 'success',
        title: noAparelho ? `${titulo} — pedido aceito` : titulo,
        message: noAparelho
          ? `Comando ${r.command_id ?? '(sem id)'}: o desfecho aparece em “O que está instalado”, sem recarregar.`
          : undefined,
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

      {loja?.configured && loja.instance_id ? (
        <Card>
          <CardHeader
            title="Loja (Play Store)"
            subtitle={`${loja.instance_id} · fonte oficial de ${pacoteDaLoja ?? loja.package} — não executa tarefas`}
            actions={<Badge icon={Store} tone={lojaLigada ? 'success' : 'muted'}>{estadoDaLoja ?? 'desconhecido'}</Badge>}
          />
          <CardBody>
            <dl className={styles.rows}>
              <div className={styles.row}>
                {/* A loja não assume mais um aplicativo: o backend passou a EXIGIR o pacote, e quem escolhe é
                    quem está olhando. Sem este seletor, o caminho da loja só existia para um app. */}
                <dt><label htmlFor="app-da-loja">Aplicativo</label></dt>
                <dd>
                  <select id="app-da-loja" value={pacoteDaLoja ?? ''}
                          onChange={(ev) => setPacoteDaLoja(ev.target.value || null)}>
                    {apps.map((a) => <option key={a.package} value={a.package}>{a.label}</option>)}
                  </select>
                  {' '}<span className={styles.detail}>{pacoteDaLoja}</span>
                </dd>
              </div>
              <div className={styles.row}>
                <dt>Na loja</dt>
                <dd>
                  {loja.store_version_code !== null
                    ? `${loja.store_version_name ?? '?'} (versionCode ${loja.store_version_code})`
                    : 'ainda não lido — ligue a loja e use “Buscar da loja”'}
                  {loja.update_available ? <> <Badge tone="warning">versão nova a buscar</Badge></> : null}
                </dd>
              </div>
              <div className={styles.row}>
                <dt>No catálogo</dt>
                <dd>{loja.catalog_version_code !== null ? `versionCode ${loja.catalog_version_code}` : 'nada catalogado ainda'}</dd>
              </div>
              <div className={styles.row}>
                <dt>Alvo do parque</dt>
                <dd>{loja.fleet_target_version_code !== null
                  ? `versionCode ${loja.fleet_target_version_code} (a maior promovida)` : 'nenhuma versão promovida ainda'}</dd>
              </div>
            </dl>
            <p className={styles.lead}>
              Entrar na conta Google e tocar em Instalar são ações suas, na <strong>janela do emulador</strong> — nenhuma
              tecla passa pelo painel. Daqui o sistema só copia o que a Play Store já instalou.
            </p>
            <div className={styles.acoes}>
              {lojaLigada ? (
                <Button size="sm" variant="ghost" icon={PowerOff}
                        onClick={() => void runInstanceAction(loja.instance_id as string, 'stop')}>Desligar a loja</Button>
              ) : (
                <Button size="sm" icon={Power}
                        onClick={() => void runInstanceAction(loja.instance_id as string, 'start')}>Ligar a loja</Button>
              )}
              <Button size="sm" variant="ghost" icon={ExternalLink}
                      disabledReason={lojaLigada ? null : 'Ligue a loja primeiro.'}
                      onClick={() => void abrirNaLoja()}>Abrir página na loja</Button>
              <Button size="sm" icon={DownloadCloud} loading={buscando}
                      disabledReason={lojaLigada ? null : 'Ligue a loja primeiro.'}
                      onClick={() => void buscarDaLoja()}>Buscar da loja</Button>
            </div>
          </CardBody>
        </Card>
      ) : null}

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
                  // `installable` só existe quando a assinatura É a confiável: o botão ali não faria nada, e mantê-lo
                  // visível parecia dizer que o clique tinha falhado. `validated` (primeira do pacote) e `invalid`
                  // (assinatura diferente da aprovada) são os casos em que aprovar tem efeito.
                  r.status === 'installable' ? (
                    <Badge tone="success" icon={ShieldCheck} title={`Assinatura confiável: ${r.signature_sha256.slice(0, 16)}…`}>
                      Assinatura aprovada
                    </Badge>
                  ) : r.status === 'validated' || r.status === 'invalid' ? (
                    <Button size="sm" variant="ghost" icon={ShieldCheck} onClick={() => void aprovar(r)}>
                      Aprovar assinatura
                    </Button>
                  ) : null
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
                  {/* O conjunto copiado da loja é o conjunto da VM-loja: x86_64/xhdpi no parque de hoje. Um
                      aparelho de outra ABI é recusado com motivo; um de outra densidade recebe o split que
                      existe, com os recursos reescalados — e isso não estava escrito em lugar nenhum. */}
                  {r.serves.length > 0 && (
                    <div className={styles.row}>
                      <dt>Serve a</dt>
                      <dd>{r.serves.join(' / ')}</dd>
                    </div>
                  )}
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
                  {r.channel === 'promoted' && (
                    <>
                      <Button size="sm" icon={Send} onClick={() => void distribuir(r, false)}>Distribuir</Button>
                      <Button size="sm" variant="ghost" icon={Zap} onClick={() => void distribuir(r, true)}>
                        Instalar em todos agora
                      </Button>
                    </>
                  )}
                  {r.channel !== 'quarantined' && (
                    <Button size="sm" variant="ghost" icon={ShieldX} onClick={() => void porEmQuarentena(r)}>
                      Quarentena
                    </Button>
                  )}
                </div>
                {entregas[r.id]?.length ? (
                  <ul className={styles.provas} aria-label={`Entrega de ${r.version_name} por aparelho`}>
                    {entregas[r.id]!.map((d) => (
                      <li key={d.id}>
                        {/* `incompatible` precisa de tom próprio: cair em "pendente" diria que a versão chega
                            depois, e ela nunca chega — o aparelho não roda esta versão e nada foi agendado. */}
                        <Badge size="sm" tone={ENTREGA_TOM[d.outcome] ?? 'neutral'}>
                          {ENTREGA_ROTULO[d.outcome] ?? 'pendente'}
                        </Badge>{' '}
                        {d.id} · {d.reason}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      <Card>
        <CardHeader title="O que está instalado em cada aparelho"
                    subtitle="Estado lido do aparelho, nunca presumido pelo código de retorno da instalação — e com a
                              data da última leitura, porque uma leitura de dias atrás não é o estado de agora." />
        <CardBody>
          {estadosMostrados.length === 0 ? (
            <p className={styles.lead}>Nenhum aplicativo catalogado nos aparelhos ainda.</p>
          ) : (
            <ul className={styles.list}>
              {estadosMostrados.map((e) => (
                <li key={`${e.instance_id}:${e.package_name}`}>
                  <Smartphone size={14} aria-hidden /> <strong>{e.instance_id}</strong> · {e.package_name}{' '}
                  <Badge tone={tom(e.state)}>{e.state}</Badge>{' '}
                  {e.observed_version_name ? `${e.observed_version_name} (${e.observed_version_code ?? '?'})` : '—'}
                  {e.drift_kind ? <> · <Badge tone="warning">{e.drift_kind}</Badge></> : null}
                  {' '}<Badge tone={idadeDaLeitura(e.verified_at).tom}>{idadeDaLeitura(e.verified_at).rotulo}</Badge>
                  {e.detail ? <span className={styles.detail}> — {e.detail}</span> : null}
                  <Button size="sm" variant="ghost" icon={RefreshCw} onClick={() => void verificar(e)}>
                    Verificar
                  </Button>
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
