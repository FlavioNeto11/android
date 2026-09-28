import {
  DownloadCloud, ExternalLink, FlaskConical, FolderInput, Hand, MonitorSmartphone, Package, Power, PowerOff,
  RefreshCw, Send, ShieldCheck, ShieldX, Smartphone, Store, TrendingUp, Undo2, Upload, Zap,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, releaseIconUrl, toApiError } from '../../api/client';
import type {
  AppCatalogEntry, AppRelease, DeviceAppState, DistributeDevice, Instance, ReleaseChannel, ReleaseTarget,
  StoreStatus,
} from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { Dialog } from '../../components/Dialog';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import type { Tone } from '../../lib/status';
import { selectStoreInstance, useAppStore } from '../../store/app';
import { chaveDoApp } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { runInstanceAction } from '../devices/actions';
import { resumoDaPromocao } from '../loja/comum';
import { toast, toastError } from '../../store/toasts';
import appStyles from '../../App.module.css';
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
export const CANAL: Record<ReleaseChannel, { rotulo: string; tom: Tone }> = {
  candidate: { rotulo: 'nunca provada', tom: 'neutral' },
  canary: { rotulo: 'em prova (canário)', tom: 'info' },
  promoted: { rotulo: 'promovida', tom: 'success' },
  quarantined: { rotulo: 'em quarentena', tom: 'danger' },
  rolled_back: { rotulo: 'substituída', tom: 'muted' },
};

function tom(estado: string): Tone {
  return ESTADO_TOM[estado] ?? 'neutral';
}

/** De ONDE o arquivo veio. `source_type` sempre existiu no tipo e nunca aparecia na tela — e a origem muda o que
 *  se pode concluir: um conjunto copiado da loja é o conjunto daquela VM (ABI e densidade dela), não um APK
 *  genérico que serve a qualquer aparelho. */
export const ORIGEM: Record<string, { rotulo: string; tom: Tone }> = {
  inbox: { rotulo: 'pasta do servidor', tom: 'neutral' },
  upload: { rotulo: 'enviado pelo painel', tom: 'neutral' },
  store: { rotulo: 'copiado da loja (Play Store)', tom: 'info' },
  builtin: { rotulo: 'app embutido', tom: 'neutral' },
};

/** Como o estado de CADA APARELHO aparece no acompanhamento da entrega. Antes a tela imprimia a string crua do
 *  banco (`verify_failed`), que só quem lê o código entende. */
export const ANDAMENTO: Record<string, { rotulo: string; tom: Tone }> = {
  installing: { rotulo: 'instalando', tom: 'info' },
  verifying: { rotulo: 'conferindo no aparelho', tom: 'info' },
  installed: { rotulo: 'instalado, falta conferir', tom: 'neutral' },
  ready: { rotulo: 'instalado e conferido', tom: 'success' },
  missing: { rotulo: 'ainda não chegou', tom: 'neutral' },
  install_failed: { rotulo: 'falhou ao instalar', tom: 'danger' },
  verify_failed: { rotulo: 'falhou ao conferir', tom: 'danger' },
  incompatible: { rotulo: 'não roda aqui', tom: 'warning' },
  version_drift: { rotulo: 'versão diferente da pedida', tom: 'warning' },
};

/**
 * "Aguardando intervenção" NÃO é um estado que o backend grave — e inventar um no banco seria pior, porque
 * ninguém o escreveria. É a leitura de dois sinais que já existem: o aparelho pediu atenção (`attention`, onde
 * caem o login e o desafio do Google) ou a entrega parou num erro que nenhuma nova tentativa automática resolve.
 * Quando isto responde texto, a tela oferece o único remédio que existe: abrir a tela daquele aparelho.
 */
export function precisaDeGente(estado: string | null | undefined, atencao: string | null | undefined): string | null {
  if (atencao) return atencao;
  if (estado === 'install_failed' || estado === 'verify_failed') {
    return 'a entrega parou aqui — abra a tela para ver o que o aparelho está mostrando';
  }
  return null;
}

/** O nome que a pessoa reconhece. Ordem: o rótulo lido do APK, o rótulo do registro de aplicativos (que cobre
 *  release catalogada antes do catálogo visual) e, por último, o pacote. */
export function nomeDoApp(r: Pick<AppRelease, 'label' | 'package_name'>, apps: AppCatalogEntry[]): string {
  return r.label || apps.find((a) => a.package === r.package_name)?.label || r.package_name;
}

/** Uma linha de "como está a entrega desta versão neste aparelho". */
interface LinhaDeEntrega {
  id: string;
  rotulo: string;
  tom: Tone;
  detalhe: string | null;
  /** Texto de "precisa de você"; `null` quando não precisa. */
  intervencao: string | null;
  /** Dá para pedir de novo? Só quando a entrega FALHOU — repetir o que está instalando não ajudaria. */
  retentavel: boolean;
}

/**
 * Aplicativos: o que foi importado da pasta `apks/inbox`, o que está instalado em cada aparelho, qual assinatura
 * foi aprovada e em que ponto do ciclo de vida cada versão está. O sistema nunca baixa APK sozinho — os arquivos
 * são colocados na pasta por uma pessoa. Canário, promoção e rollback são pedidos daqui; depois de promover (ou
 * voltar), cada aparelho que tem o app persegue a promovida sozinho (ADR-026).
 */
/** `embutida`: dentro do menu Aplicativos (aba Versões e instalação) — sem o título da página. */
export function ReleasesPage({ embutida = false }: { embutida?: boolean } = {}) {
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
  // "Instalar em…": os destinos vêm do BACKEND com a compatibilidade já julgada. `alvos === null` = ainda
  // carregando. Sem esta tela, escolher destino não existia: ou um aparelho de canário, ou o parque inteiro.
  const [destinos, setDestinos] = useState<
    { release: AppRelease; alvos: ReleaseTarget[] | null; escolhidos: Set<string> } | null>(null);
  const [enviando, setEnviando] = useState(false);
  const arquivoRef = useRef<HTMLInputElement>(null);
  const [workerLocal, setWorkerLocal] = useState<string | null>(null);
  const abrirTela = useUiStore((s) => s.openFocus);
  const token = useRef(0);

  const carregar = useCallback(async () => {
    const meu = ++token.current;
    const [r, e, i, a, w] = await Promise.allSettled([
      api.listReleases(), api.listAppState(), api.listInstances(), api.listAppCatalog(), api.workers()]);
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
    // ONDE a loja roda decide o que a tela pode prometer: numa VM desta máquina há janela do emulador para
    // digitar a conta Google; numa VM de outro servidor não há — e o painel recusa texto na loja de propósito.
    if (w.status === 'fulfilled') setWorkerLocal(w.value.find((x) => x.local)?.id ?? null);
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
  const aparelhosVivos = useAppStore((s) => s.instances);
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
          + 'aparelho continua como está e o pedido volta aqui pedindo a reinstalação de propósito. A versão de onde '
          + 'ele sai vira "substituída" para o parque inteiro, e os outros aparelhos nela voltam sozinhos para a '
          + 'promovida anterior.',
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

  // ------------------------------------------------------------------ destinos: instalar em quem eu escolher
  async function abrirDestinos(r: AppRelease) {
    setDestinos({ release: r, alvos: null, escolhidos: new Set() });
    try {
      const { targets } = await api.releaseTargets(r.id);
      // Pré-seleção honesta: quem PODE receber AGORA e ainda não está nesta versão. Aparelho desligado fica
      // desmarcado de propósito — a rota de instalação recusa quem não está online, e marcá-lo por padrão
      // devolveria uma parede de recusas. Quem não roda a versão entra travado, com o motivo do backend ao
      // lado: a limitação é explicada ANTES de enviar, não no meio.
      setDestinos({
        release: r, alvos: targets,
        escolhidos: new Set(targets.filter((t) => t.compatible && !t.already && t.state === 'online')
          .map((t) => t.id)),
      });
    } catch (e) {
      setDestinos(null);
      toastError('Não foi possível listar os destinos', e);
    }
  }

  async function instalarNosEscolhidos() {
    const atual = destinos;
    if (!atual?.alvos) return;
    const escolhidos = atual.alvos.filter((t) => atual.escolhidos.has(t.id) && t.compatible);
    setDestinos(null);
    const aceitos: string[] = [];
    const recusados: string[] = [];
    for (const t of escolhidos) {
      try {
        await api.installApp(t.id, atual.release.id);
        aceitos.push(t.id);
      } catch (e) {
        // Um destino recusado NÃO impede os outros: o aparelho desligado ou ocupado é o caso comum, e abortar
        // o lote por causa dele faria o operador repetir a escolha inteira.
        recusados.push(`${t.id}: ${toApiError(e).message}`);
      }
    }
    toast({
      tone: aceitos.length ? 'info' : 'warning',
      title: aceitos.length ? `Instalação pedida em ${aceitos.length} aparelho(s)` : 'Nenhum aparelho aceitou o pedido',
      // Aceito não é instalado: leva minutos, e cada aparelho tem o seu próprio desfecho, que aparece na
      // lista "Entrega por aparelho" sem recarregar.
      message: recusados.length ? `Recusados — ${recusados.join(' · ')}` : 'O andamento de cada um aparece abaixo, sozinho.',
    });
    await carregar();
  }

  /** Progresso e falha POR APARELHO desta versão. O estado vivo (evento `app_state.updated`) manda; a resposta
   *  do pedido só preenche quem ainda não tem linha no banco — é ela que explica o "não roda aqui". */
  function progressoDe(r: AppRelease): LinhaDeEntrega[] {
    const linhas = new Map<string, LinhaDeEntrega>();
    for (const d of entregas[r.id] ?? []) {
      linhas.set(d.id, {
        id: d.id, tom: ENTREGA_TOM[d.outcome] ?? 'neutral', rotulo: ENTREGA_ROTULO[d.outcome] ?? 'pendente',
        detalhe: d.reason, intervencao: null, retentavel: false,
      });
    }
    for (const e of estadosMostrados) {
      if (e.package_name !== r.package_name) continue;
      if (e.desired_release_id !== r.id && e.installed_release_id !== r.id) continue;
      const desc = ANDAMENTO[e.state] ?? { rotulo: e.state, tom: tom(e.state) };
      // `attention` vem do estado VIVO do painel, não da lista carregada ao montar: "precisa de você" que só
      // aparece quando alguém recarrega a página não serve para nada.
      const atencao = aparelhosVivos[e.instance_id]?.attention ?? null;
      linhas.set(e.instance_id, {
        id: e.instance_id, rotulo: desc.rotulo, tom: desc.tom, detalhe: e.detail,
        intervencao: precisaDeGente(e.state, atencao),
        retentavel: e.state === 'install_failed' || e.state === 'verify_failed',
      });
    }
    return [...linhas.values()].sort((a, b) => a.id.localeCompare(b.id));
  }

  async function tentarDeNovo(instanceId: string, r: AppRelease) {
    try {
      await api.installApp(instanceId, r.id);
      toast({ tone: 'info', title: `Nova tentativa pedida em ${instanceId}`,
              message: 'O desfecho aparece nesta mesma lista, sem recarregar.' });
    } catch (e) {
      toastError('O pedido foi recusado', e);
    }
  }

  // ------------------------------------------------------------------ upload: entrada de APK sem acesso ao disco
  async function enviarArquivos(lista: FileList | null) {
    const arquivos = [...(lista ?? [])].filter((f) => f.name.toLowerCase().endsWith('.apk'));
    if (arquivos.length === 0) {
      toastError('Nada para enviar', new Error('Escolha um ou mais arquivos .apk (o conjunto inteiro de uma vez).'));
      return;
    }
    // Um conjunto de splits é uma unidade: todos os arquivos vão com o MESMO identificador, e só o último manda
    // importar. Importar a cada arquivo reprovaria o conjunto por "falta o base.apk".
    const conjunto = `${Date.now().toString(36)}`;
    setEnviando(true);
    try {
      let ultimo: Awaited<ReturnType<typeof api.uploadRelease>> | null = null;
      for (let i = 0; i < arquivos.length; i += 1) {
        ultimo = await api.uploadRelease(arquivos[i]!, conjunto, i === arquivos.length - 1);
      }
      const imp = ultimo?.imported ?? null;
      toast({
        tone: imp?.ok ? 'success' : 'warning',
        title: imp?.ok ? `${imp.package} ${imp.version_name} catalogado` : 'O conjunto enviado não foi aceito',
        message: imp?.ok
          ? 'Pacote, versão, splits e assinatura saíram do próprio arquivo. Enviar não instala nada: a assinatura '
            + 'ainda precisa da sua aprovação, e a versão, de uma prova num aparelho.'
          : (imp?.reason ?? 'O servidor não explicou o motivo.'),
      });
      await carregar();
    } catch (e) {
      toastError('O envio falhou', e);
    } finally {
      setEnviando(false);
      if (arquivoRef.current) arquivoRef.current.value = '';
    }
  }

  // ------------------------------------------------------------------ a loja como fonte
  const estadoDaLoja = lojaViva?.state ?? loja?.state ?? null;
  const lojaLigada = estadoDaLoja === 'online';
  // Em QUE máquina a VM da loja roda. Enquanto a loja só podia ser emulador desta máquina, a tela podia dizer
  // "na janela do emulador" sem pensar; com a loja num worker, essa janela está na área de trabalho DE OUTRA
  // MÁQUINA — e o painel continua recusando texto na loja de propósito (a conta Google cairia em `adb shell
  // input text '<senha>'`, com o segredo na linha de comando do host).
  const servidorDaLoja = lojaViva?.worker_id ?? null;
  const lojaRemota = servidorDaLoja !== null && workerLocal !== null && servidorDaLoja !== workerLocal;

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
      const promocao = body.verb === 'promote' ? resumoDaPromocao(r.devices) : undefined;
      // `promote`/`quarantine` são decisões de banco: decidiram AGORA, e verde é honesto. Canário, rollback e
      // instalação mexem no aparelho e voltam apenas ACEITOS — verde ali chamava de sucesso o que só tinha sido
      // aceito. O desfecho chega pelo comando e por `app_state.updated`, e a lista abaixo se atualiza sozinha.
      const noAparelho = Boolean(body.instance_id);
      toast({
        tone: noAparelho ? 'info' : 'success',
        title: noAparelho ? `${titulo} — pedido aceito` : titulo,
        message: noAparelho
          ? `Comando ${r.command_id ?? '(sem id)'}: o desfecho aparece em “O que está instalado”, sem recarregar.`
          : promocao,
      });
    } catch (e) {
      toastError('O pedido foi recusado', e);
    }
  }

  // Constantes locais porque o TypeScript perde o estreitamento de `destinos.alvos` dentro dos callbacks do
  // JSX: propriedade de objeto não continua narrowed dentro de uma arrow function.
  const alvosDoDialogo = destinos?.alvos ?? null;
  const canalDoDialogo = destinos?.release.channel ?? null;
  const escolhidosDoDialogo = destinos?.escolhidos ?? null;

  if (releases === null) {
    return (
      <LoadingRegion label="Carregando aplicativos…">
        <Skeleton height={120} />
      </LoadingRegion>
    );
  }

  return (
    // Embutida, a página JÁ é a de Aplicativos (que aplica `appStyles.page`): repetir o invólucro dobrava o recuo
    // lateral e criava um segundo contêiner `page` dentro do primeiro.
    <div className={embutida ? styles.page : `${appStyles.page} ${styles.page}`}>
      <div className={appStyles.pageHeader}>
        <div>
          {embutida ? <h2 className={appStyles.pageTitle}>Versões e instalação</h2> : <h1 className={appStyles.pageTitle}>Aplicativos</h1>}
          <p className={appStyles.pageLead}>
            Envie o APK pelo painel ou coloque os arquivos em <code>apks/inbox</code> e importe. O sistema lê nome,
            ícone, pacote, versão, splits, ABIs e assinatura do próprio arquivo, guarda uma cópia imutável e confere
            o hash antes de cada instalação. Uma versão só é promovida depois de instalar e abrir num aparelho de
            prova — e você escolhe para quais aparelhos ela vai.
          </p>
        </div>
        <div className={styles.acoes}>
          {/* Até aqui a ÚNICA entrada de APK era largar arquivo numa pasta da máquina do backend: quem abre o
              painel de outro computador não tinha caminho nenhum. O arquivo enviado passa pela mesma inspeção. */}
          <input ref={arquivoRef} type="file" accept=".apk" multiple hidden
                 data-testid="entrada-de-apk"
                 onChange={(ev) => void enviarArquivos(ev.target.files)} />
          <Button variant="ghost" icon={Upload} loading={enviando} onClick={() => arquivoRef.current?.click()}>
            Enviar APK
          </Button>
          <Button icon={FolderInput} loading={importando} onClick={() => void importar()}>
            Importar da pasta
          </Button>
        </div>
      </div>

      {loja?.configured && loja.instance_id ? (
        <Card>
          <CardHeader
            title="Loja (Play Store)"
            subtitle={`${loja.instance_id} · ${lojaRemota ? `no servidor ${servidorDaLoja}` : 'nesta máquina'}`
              + ` · fonte oficial de ${pacoteDaLoja ?? loja.package} — não executa tarefas`}
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
              {lojaRemota ? (
                <>
                  Entrar na conta Google e tocar em Instalar são ações suas, na janela do emulador —{' '}
                  <strong>que está na área de trabalho do servidor {servidorDaLoja}</strong>, não nesta máquina.
                  Toque e tecla passam pelo painel (use “Abrir a tela da loja”); <strong>texto, não</strong>: digitar a
                  senha por aqui cairia em <code>adb shell input text</code>, com o segredo visível na linha de
                  comando daquela máquina. Para digitar a conta, use um acesso remoto à área de trabalho do servidor.
                </>
              ) : (
                <>
                  Entrar na conta Google e tocar em Instalar são ações suas, na <strong>janela do emulador</strong> desta
                  máquina — nenhuma tecla passa pelo painel. Daqui o sistema só copia o que a Play Store já instalou.
                </>
              )}
            </p>
            {/* A regra que nenhuma tela dizia, e que o pedido exige que seja dita: conta de uma VM não vale em
                outra. É por isso que o desenho copia o APK por adb em vez de "entrar com a conta" em cada
                aparelho — e quem não sabe disso tenta a segunda coisa. */}
            <p className={styles.lead}>
              <Badge tone="warning" icon={Hand}>uma conta por VM</Badge>{' '}
              A conta Google entrou <strong>só nesta VM-loja</strong> e vale só nela. Nenhum outro aparelho do parque
              herda essa conta: o que a loja instalou é copiado por ADB, como arquivo. Uma segunda loja, em outro
              servidor, precisa da própria autenticação — e o que ela instalar só é copiado a partir dela.
            </p>
            <div className={styles.acoes}>
              {lojaLigada ? (
                <Button size="sm" variant="ghost" icon={PowerOff}
                        onClick={() => void runInstanceAction(loja.instance_id as string, 'stop')}>Desligar a loja</Button>
              ) : (
                <Button size="sm" icon={Power}
                        onClick={() => void runInstanceAction(loja.instance_id as string, 'start')}>Ligar a loja</Button>
              )}
              {/* A loja só era alcançável pela grade de aparelhos. Frame e toque já funcionavam pelo túnel: o
                  que faltava era o atalho — e é ele que torna possível operar a loja de um worker remoto. */}
              <Button size="sm" variant="ghost" icon={MonitorSmartphone}
                      disabledReason={lojaLigada ? null : 'Ligue a loja primeiro.'}
                      onClick={() => abrirTela(loja.instance_id as string)}>Abrir a tela da loja</Button>
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
          actions={<>
            <Button variant="ghost" icon={Upload} loading={enviando} onClick={() => arquivoRef.current?.click()}>Enviar APK</Button>
            <Button icon={FolderInput} loading={importando} onClick={() => void importar()}>Importar da pasta</Button>
          </>}
        >
          O catálogo está vazio.
        </EmptyState>
      ) : (
        <div className={styles.grid}>
          {releases.map((r) => (
            <Card key={r.id}>
              <CardHeader
                title={
                  <span className={styles.appTitulo}>
                    {/* Ícone e nome saem do PRÓPRIO APK, na mesma leitura que já dava pacote e versão. Antes o
                        cartão dizia só `com.instagram.android 447.0.0`: identidade técnica, não o que a pessoa
                        reconhece. Sem ícone servível (o caso do ícone adaptativo em XML) o cartão cai no
                        símbolo genérico — é melhor do que uma imagem quebrada. */}
                    {r.has_icon
                      ? <img className={styles.icone} src={releaseIconUrl(r.id)} alt="" width={28} height={28} />
                      : <Package size={20} className={styles.iconeVazio} aria-hidden />}
                    <span>{nomeDoApp(r, apps)} {r.version_name}</span>
                  </span>
                }
                subtitle={<>
                  <span className={styles.mono}>{r.package_name}</span>
                  {` · versionCode ${r.version_code} · ${r.artifact_type} · `}
                  <Badge size="sm" tone={ORIGEM[r.source_type]?.tom ?? 'neutral'}>
                    {ORIGEM[r.source_type]?.rotulo ?? r.source_type}
                  </Badge>
                  {r.source_reference ? <span className={styles.detail}> — {r.source_reference}</span> : null}
                </>}
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
                  {/* Escolher DESTINOS é o caminho novo: até aqui só existiam "um aparelho de canário" e "o
                      parque inteiro" — e "todos" é literal, inclusive os aparelhos que são só de QA e os de
                      outro servidor, cada um recebendo o conjunto pelo túnel. */}
                  {r.status === 'installable' && r.channel !== 'quarantined' && (
                    <Button size="sm" variant="ghost" icon={Smartphone} onClick={() => void abrirDestinos(r)}>
                      Instalar em…
                    </Button>
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
                {/* Entrega POR APARELHO, ao vivo. A lista antiga era a resposta do POST, congelada no instante
                    do clique: ela dizia "instalando" para sempre. Agora o estado vivo (evento `app_state.updated`)
                    manda, e a resposta do pedido só explica quem nem chegou a ser agendado. */}
                {progressoDe(r).length > 0 ? (
                  <ul className={styles.entrega} aria-label={`Entrega de ${r.version_name} por aparelho`}>
                    {progressoDe(r).map((d) => (
                      <li key={d.id}>
                        {/* `incompatible` precisa de tom próprio: cair em "pendente" diria que a versão chega
                            depois, e ela nunca chega — o aparelho não roda esta versão e nada foi agendado. */}
                        <Badge size="sm" tone={d.tom}>{d.rotulo}</Badge>{' '}
                        <strong>{d.id}</strong>{d.detalhe ? ` · ${d.detalhe}` : ''}
                        {d.intervencao ? (
                          <>
                            {' '}
                            <Badge size="sm" tone="warning" icon={Hand}>aguardando intervenção</Badge>{' '}
                            <span className={styles.detail}>{d.intervencao}</span>{' '}
                            <Button size="sm" variant="ghost" icon={MonitorSmartphone} onClick={() => abrirTela(d.id)}>
                              Abrir a tela
                            </Button>
                          </>
                        ) : null}
                        {d.retentavel ? (
                          <Button size="sm" variant="ghost" icon={RefreshCw} onClick={() => void tentarDeNovo(d.id, r)}>
                            Tentar de novo
                          </Button>
                        ) : null}
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

      {/* ------------------------------------------------------------ "Instalar em…" */}
      <Dialog
        open={destinos !== null}
        onClose={() => setDestinos(null)}
        size="md"
        icon={Smartphone}
        title={destinos ? `Instalar ${nomeDoApp(destinos.release, apps)} ${destinos.release.version_name} em…` : ''}
        footer={
          <>
            <Button variant="ghost" onClick={() => setDestinos(null)}>Cancelar</Button>
            <Button
              icon={Send}
              disabledReason={destinos?.escolhidos.size ? null : 'Escolha ao menos um aparelho.'}
              onClick={() => void instalarNosEscolhidos()}
            >
              Instalar em {destinos?.escolhidos.size ?? 0} aparelho(s)
            </Button>
          </>
        }
      >
        {alvosDoDialogo === null || escolhidosDoDialogo === null ? (
          <Skeleton height={120} />
        ) : alvosDoDialogo.length === 0 ? (
          <p className={styles.lead}>Nenhum aparelho de tarefa cadastrado. A loja não entra aqui: ela é a fonte
            do aplicativo, nunca o destino.</p>
        ) : (
          <>
            <p className={styles.lead}>
              Quem não roda esta versão aparece travado, com o motivo — a limitação é explicada <strong>antes</strong> de
              enviar, e não no meio, como um <code>INSTALL_FAILED_NO_MATCHING_ABIS</code> depois de a tela já ter
              dito “aceito”. Cada aparelho recebe um pedido próprio, com desfecho próprio.
            </p>
            {/* Escolher destinos não é promover: o canário existe justamente para descobrir, num aparelho só,
                que a versão instala e abre. Instalar em vários antes disso é decisão de quem está olhando, mas
                não pode ser silenciosa. */}
            {canalDoDialogo !== 'promoted' ? (
              <p className={styles.lead}>
                <Badge tone="warning">ainda não provada</Badge>{' '}
                Esta versão não provou que abre num aparelho. O canário existe para isso: instalar em vários
                antes da prova espalha o problema em vez de encontrá-lo.
              </p>
            ) : null}
            {[...new Set(alvosDoDialogo.map((t) => t.worker_id ?? ''))].sort().map((servidor) => (
              <fieldset key={servidor || 'local'} className={styles.grupo}>
                {/* Agrupado por SERVIDOR porque a decisão não é a mesma: mandar um conjunto de 243 MB para seis
                    aparelhos de outra máquina atravessa o túnel seis vezes. */}
                <legend>{servidor ? `Servidor ${servidor}` : 'Sem servidor registrado'}</legend>
                <ul className={styles.list}>
                  {alvosDoDialogo.filter((t) => (t.worker_id ?? '') === servidor).map((t) => (
                    <li key={t.id}>
                      <label>
                        <input
                          type="checkbox"
                          disabled={!t.compatible}
                          checked={escolhidosDoDialogo.has(t.id)}
                          onChange={(ev) => setDestinos((d) => {
                            if (!d) return d;
                            const escolhidos = new Set(d.escolhidos);
                            if (ev.target.checked) escolhidos.add(t.id); else escolhidos.delete(t.id);
                            return { ...d, escolhidos };
                          })}
                        />{' '}
                        <strong>{t.id}</strong>
                      </label>{' '}
                      <Badge size="sm" tone={t.state === 'online' ? 'success' : 'muted'}>{t.state}</Badge>{' '}
                      {t.state !== 'online' && t.compatible ? (
                        <span className={styles.detail}>ligue-o antes: a instalação por destino só aceita
                          aparelho online{' '}</span>
                      ) : null}
                      {t.compatible
                        ? (t.already
                            ? <Badge size="sm" tone="success">já está nesta versão</Badge>
                            : <span className={styles.detail}>
                                {t.installed_version_name ? `hoje: ${t.installed_version_name}` : 'sem este app hoje'}
                              </span>)
                        : <><Badge size="sm" tone="warning">não roda aqui</Badge>{' '}
                           <span className={styles.detail}>{t.reason}</span></>}
                    </li>
                  ))}
                </ul>
              </fieldset>
            ))}
          </>
        )}
      </Dialog>
    </div>
  );
}
