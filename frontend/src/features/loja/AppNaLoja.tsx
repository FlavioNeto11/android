/**
 * A página de UM aplicativo na loja: versões com o ciclo de vida, aparelhos com o que cada um tem, e os dois
 * caminhos que faltavam — distribuir para quem eu quiser (com prévia) e atualizar quem ficou na versão antiga.
 *
 * Tudo o que decide vem do backend: a prévia e a compatibilidade (`distribute` com `dry_run`, `targets`), o estado
 * de cada aparelho (`/app-state` e o evento `app_state.updated`, ao vivo). Aceito não é instalado: o desfecho de
 * cada aparelho chega pela tabela, sozinho.
 */
import {
  ArrowLeft, DownloadCloud, ExternalLink, FlaskConical, RefreshCw, Send, ShieldCheck, ShieldX, TrendingUp, Undo2,
  Upload,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, toApiError } from '../../api/client';
import type {
  AppCategory, AppRelease, AppStoreEntry, DeviceAppState, DistributeDevice, Instance, StoreStatus,
} from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { Checkbox, Select } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { useAppStore } from '../../store/app';
import { chaveDoApp } from '../../store/reducer';
import { toast, toastError } from '../../store/toasts';
import { ANDAMENTO, CANAL, ORIGEM, idadeDaLeitura } from '../releases/ReleasesPage';
import { Termo } from '../apps/glossario';
import { DistribuirDialog } from './DistribuirDialog';
import { CATEGORIAS, IconeDoApp, resumoDaPromocao } from './comum';
import styles from './Loja.module.css';

const STATUS: Record<string, { rotulo: string; tom: 'success' | 'neutral' | 'danger' | 'warning' }> = {
  installable: { rotulo: 'instalável', tom: 'success' },
  validated: { rotulo: 'assinatura por aprovar', tom: 'warning' },
  invalid: { rotulo: 'bloqueada', tom: 'danger' },
  incompatible: { rotulo: 'incompatível', tom: 'danger' },
  imported: { rotulo: 'importada', tom: 'neutral' },
  inspected: { rotulo: 'inspecionada', tom: 'neutral' },
};

interface Props {
  entry: AppStoreEntry;
  onBack: () => void;
  onChanged: () => void;
}

export function AppNaLoja({ entry, onBack, onChanged }: Props) {
  const pkg = entry.package;
  const [releases, setReleases] = useState<AppRelease[] | null>(null);
  const [estados, setEstados] = useState<DeviceAppState[]>([]);
  const [aparelhos, setAparelhos] = useState<Instance[]>([]);
  const [loja, setLoja] = useState<StoreStatus | null>(null);
  const [provaEm, setProvaEm] = useState<Record<string, string>>({});
  const [distribuir, setDistribuir] = useState<{ release: AppRelease; pre?: string[] } | null>(null);
  const [ultimaEntrega, setUltimaEntrega] = useState<Record<string, DistributeDevice>>({});
  const [selecionados, setSelecionados] = useState<Set<string>>(new Set());
  const [enviando, setEnviando] = useState(false);
  const arquivoRef = useRef<HTMLInputElement>(null);
  const token = useRef(0);

  const carregar = useCallback(async () => {
    const meu = ++token.current;
    const [r, e, i, l] = await Promise.allSettled([
      api.listReleases(pkg), api.listAppState(pkg), api.listInstances(), api.storeStatus(pkg)]);
    if (meu !== token.current) return;
    if (r.status === 'fulfilled') setReleases(r.value);
    else { setReleases([]); toastError('Não foi possível listar as versões', r.reason); }
    if (e.status === 'fulfilled') setEstados(e.value);
    else toastError('Não foi possível ler o que está instalado nos aparelhos', e.reason);
    if (i.status === 'fulfilled') setAparelhos(i.value.filter((x) => x.kind !== 'store'));
    else toastError('Não foi possível listar os aparelhos', i.reason);
    setLoja(l.status === 'fulfilled' ? l.value : null);
  }, [pkg]);

  useEffect(() => { void carregar(); }, [carregar]);

  // Estado AO VIVO: o evento `app_state.updated` substitui a foto do carregamento, aparelho por aparelho.
  const aoVivo = useAppStore((s) => s.appState);
  const estadoDe = useCallback((iid: string): DeviceAppState | null => {
    return aoVivo[chaveDoApp(iid, pkg)] ?? estados.find((x) => x.instance_id === iid) ?? null;
  }, [aoVivo, estados, pkg]);

  // A MESMA escolha do backend (`promoted_release`, ADR-026): maior versão; no empate, a promovida por último e
  // depois o maior id. Com duas promovidas de mesmo número, a página mostrava a que a API listasse primeiro.
  const promovida = useMemo(() => (releases ?? [])
    .filter((r) => r.channel === 'promoted' && r.status === 'installable')
    .sort((a, b) => b.version_code - a.version_code
      || (b.channel_at ?? '').localeCompare(a.channel_at ?? '')
      || (a.id < b.id ? 1 : a.id > b.id ? -1 : 0))[0] ?? null, [releases]);
  const porId = useMemo(() => new Map((releases ?? []).map((r) => [r.id, r])), [releases]);

  /** Quem tem o app numa versão MENOR que a promovida: é quem "Atualizar" alcança. */
  const atrasados = useMemo(() => {
    if (!promovida) return [];
    return aparelhos.filter((a) => {
      const e = estadoDe(a.id);
      if (!e) return false;
      const codigo = (e.installed_release_id ? porId.get(e.installed_release_id)?.version_code : null)
        ?? e.observed_version_code;
      return codigo != null && codigo < promovida.version_code;
    }).map((a) => a.id);
  }, [aparelhos, estadoDe, porId, promovida]);

  async function mudarCategoria(valor: string) {
    try {
      await api.updateApp(entry.app_id, { category: (valor || null) as AppCategory | null });
      toast({ tone: 'success', title: 'Categoria atualizada' });
      onChanged();
    } catch (e) {
      toastError('Não foi possível mudar a categoria', e);
    }
  }

  async function enviar(lista: FileList | null) {
    const arquivos = [...(lista ?? [])].filter((f) => /\.(apk|apks|xapk|apkm)$/i.test(f.name));
    if (arquivos.length === 0) {
      toast({ tone: 'warning', title: 'Nada para enviar', message: 'Escolha os .apk do conjunto, ou um .xapk/.apks/.apkm sozinho.' });
      return;
    }
    setEnviando(true);
    // O carimbo vai NA FRENTE: truncado a 60, um pacote longo cortaria o carimbo e dois envios dividiriam a pasta.
    const conjunto = `${Date.now()}-${pkg.replace(/[^A-Za-z0-9._-]/g, '')}`.slice(0, 60);
    try {
      let ultimo: Awaited<ReturnType<typeof api.uploadRelease>> | null = null;
      for (let i = 0; i < arquivos.length; i++) {
        ultimo = await api.uploadRelease(arquivos[i]!, conjunto, i === arquivos.length - 1,
          `enviado pela loja do painel para ${pkg}`);
      }
      const imp = ultimo?.imported;
      if (imp && !imp.ok) {
        toastError('O arquivo foi recusado', new Error(imp.reason ?? 'sem motivo'));
      } else if (imp && imp.package !== pkg) {
        toast({ tone: 'warning', title: `O arquivo é de outro app (${imp.package})`,
                message: 'Ele foi catalogado no app certo, que aparece na vitrine.' });
      } else {
        toast({ tone: 'success', title: `Versão ${imp?.version_name ?? ''} recebida`,
                message: 'Pacote, versão e assinatura vêm do próprio arquivo. Enviar não instala nada.' });
      }
      await carregar();
      onChanged();
    } catch (e) {
      toastError('O envio falhou', e);
    } finally {
      setEnviando(false);
      if (arquivoRef.current) arquivoRef.current.value = '';
    }
  }

  async function lojaAbrir() {
    try {
      await api.storeOpenListing(pkg);
      toast({ tone: 'info', title: 'Página aberta na Play Store da loja',
              message: 'Instalar ou atualizar lá é um toque seu, com a sua conta. Depois use "Copiar da loja".' });
    } catch (e) {
      toastError('Não foi possível abrir a Play Store', e);
    }
  }

  async function lojaCopiar() {
    try {
      const r = await api.storeSync(pkg);
      toast({ tone: 'info', title: 'Copiando da loja', message: `Comando ${r.command_id}: a versão aparece aqui ao terminar.` });
    } catch (e) {
      toastError('Não foi possível copiar da loja', e);
    }
  }

  async function aprovar(r: AppRelease) {
    const { confirmed, note } = await confirm({
      title: `Aprovar a assinatura de ${entry.name}?`,
      body: `A assinatura ${r.signature_sha256.slice(0, 16)}… passa a ser a confiável para este app. Versão futura `
        + 'com outra assinatura fica bloqueada até nova aprovação. Isto não prova a origem do arquivo, só que ele '
        + 'continua sendo do mesmo autor.',
      confirmLabel: 'Aprovar assinatura',
      note: { label: 'De onde veio o arquivo', placeholder: 'ex.: Play Store da loja em 26/09' },
    });
    if (!confirmed) return;
    try {
      await api.approveSignature(r.id, note || undefined);
      toast({ tone: 'success', title: 'Assinatura aprovada' });
      await carregar();
      onChanged();
    } catch (e) {
      toastError('Não foi possível aprovar', e);
    }
  }

  async function ciclo(r: AppRelease, body: Parameters<typeof api.releaseLifecycle>[1], titulo: string) {
    try {
      const resposta = await api.releaseLifecycle(r.id, body);
      if (body.verb === 'promote' && resposta.devices) {
        setUltimaEntrega(Object.fromEntries(resposta.devices.map((d) => [d.id, d])));
      }
      toast({ tone: body.verb === 'promote' || body.verb === 'quarantine' ? 'success' : 'info', title: titulo,
              message: body.verb === 'canary' ? 'A prova roda no aparelho; o desfecho aparece na tabela.'
                : body.verb === 'promote' ? resumoDaPromocao(resposta.devices) : undefined });
      await carregar();
      onChanged();
    } catch (e) {
      toastError('O pedido foi recusado', e);
    }
  }

  async function canario(r: AppRelease) {
    const iid = provaEm[r.id] || r.canary_instance_id || aparelhos.find((a) => a.state === 'online')?.id;
    if (!iid) {
      toast({ tone: 'warning', title: 'Sem aparelho', message: 'Ligue um aparelho para a prova.' });
      return;
    }
    const { confirmed } = await confirm({
      title: `Provar ${r.version_name} no ${iid}?`,
      body: 'A versão é instalada num aparelho só e só conta como boa se instalar e abrir. Falhando, vai para a '
        + 'quarentena sozinha. Promover depende dessa prova.',
      confirmLabel: 'Colocar em prova',
      danger: r.channel === 'quarantined',
    });
    if (!confirmed) return;
    await ciclo(r, { verb: 'canary', instance_id: iid }, `Prova começou em ${iid}`);
  }

  async function quarentena(r: AppRelease) {
    const { confirmed, note } = await confirm({
      title: `Bloquear ${r.version_name}?`,
      body: 'A instalação desta versão fica bloqueada. Nada é apagado e nenhum aparelho muda sozinho: quem já está '
        + 'nela continua até você pedir a volta.',
      confirmLabel: 'Pôr em quarentena', danger: true,
      note: { label: 'Motivo', placeholder: 'ex.: travou ao abrir' },
    });
    if (!confirmed) return;
    await ciclo(r, { verb: 'quarantine', note: note || undefined }, 'Versão em quarentena');
  }

  async function voltarSelecionados() {
    const alvos = [...selecionados].map((id) => ({ id, e: estadoDe(id) }))
      .filter((x) => x.e?.previous_release_id && (x.e.installed_release_id ?? x.e.desired_release_id));
    const sem = selecionados.size - alvos.length;
    if (alvos.length === 0) {
      // Aviso local, não erro de rede: `toastError` acrescentaria "o backend não respondeu", que aqui é falso.
      toast({ tone: 'warning', title: 'Nada para voltar', message: 'Nenhum aparelho selecionado tem versão anterior registrada.' });
      return;
    }
    const { confirmed, note } = await confirm({
      title: `Voltar a versão anterior em ${alvos.length} aparelho(s)?`,
      body: 'Cada aparelho volta para a versão que tinha antes, PRESERVANDO os dados. Se o Android recusar voltar '
        + 'sem apagar, nada é apagado: o aparelho fica como está e aparece na tabela pedindo a reinstalação de '
        + 'propósito. Aparelho desligado recusa: ligue-o e repita. Atenção: a versão de onde o aparelho sai passa '
        + 'a "substituída" para o PARQUE inteiro e deixa de ser a promovida — os outros aparelhos que estão nela '
        + 'voltam sozinhos para a promovida anterior (o ligado e livre já, o desligado quando ligar).'
        + (sem ? ` ${sem} selecionado(s) sem versão anterior ficam de fora.` : ''),
      confirmLabel: 'Voltar versão',
      note: { label: 'Motivo', placeholder: 'ex.: a nova versão trava no feed' },
    });
    if (!confirmed) return;
    const aceitos: string[] = [];
    const recusados: string[] = [];
    for (const { id, e } of alvos) {
      try {
        await api.releaseLifecycle((e!.installed_release_id ?? e!.desired_release_id)!,
          { verb: 'rollback', instance_id: id, note: note || undefined });
        aceitos.push(id);
      } catch (err) {
        // Um aparelho recusado não impede os outros: desligado ou ocupado é o caso comum.
        recusados.push(`${id}: ${toApiError(err).message}`);
      }
    }
    toast({ tone: aceitos.length ? 'info' : 'warning',
            title: aceitos.length ? `Volta pedida em ${aceitos.length} aparelho(s)` : 'Nenhum aparelho aceitou',
            message: recusados.length ? `Recusados — ${recusados.join(' · ')}` : 'O desfecho aparece na tabela.' });
  }

  async function verificar(iid: string) {
    try {
      await api.verifyApp(iid, pkg);
      toast({ tone: 'info', title: `Relendo ${entry.name} no ${iid}` });
    } catch (e) {
      toastError('Não foi possível reler o aparelho', e);
    }
  }

  if (releases === null) {
    return <LoadingRegion label="Carregando o aplicativo…"><Skeleton height={240} /></LoadingRegion>;
  }
  const todosSelecionados = aparelhos.length > 0 && selecionados.size === aparelhos.length;

  return (
    <div className={styles.detail}>
      <div className={styles.detailHead}>
        <Button size="sm" variant="ghost" icon={ArrowLeft} onClick={onBack}>Loja</Button>
        <div className={styles.cardHead}>
          <IconeDoApp entry={entry} grande />
          <div className={styles.title}>
            <h2 className={styles.detailTitle}>{entry.name}</h2>
            <code>{pkg}</code>
          </div>
        </div>
        <div className={styles.badges}>
          {promovida ? <Badge tone="success">{promovida.version_name} <Termo termo="promovida">promovida</Termo></Badge>
            : <Badge tone="warning"><Termo termo="sem-promovida">nenhuma versão promovida</Termo></Badge>}
          <Badge tone={entry.has_catalog ? 'success' : 'neutral'}>
            <Termo termo={entry.has_catalog ? 'catalogo-de-acoes' : 'ia-livre'}>
              {entry.has_catalog ? 'IA com catálogo de ações' : 'IA pelo caminho livre'}</Termo></Badge>
        </div>
        <span className={styles.grow} />
        <Select aria-label="Categoria" small className={styles.inlineSelect} value={entry.category ?? ''} onChange={(e) => void mudarCategoria(e.target.value)}>
          <option value="">Sem categoria</option>
          {Object.entries(CATEGORIAS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </Select>
      </div>

      <Card>
        <CardHeader title="Nova versão"
                    subtitle="O APK que você fornece, ou o que você instalou pela Play Store da loja com a sua conta. Nunca de espelho de terceiros." />
        <CardBody>
          <div className={styles.actions}>
            <input ref={arquivoRef} type="file" hidden multiple accept=".apk,.apks,.xapk,.apkm"
                   onChange={(e) => void enviar(e.target.files)} />
            <Button icon={Upload} loading={enviando} onClick={() => arquivoRef.current?.click()}>Enviar APK / XAPK</Button>
            {loja?.configured ? (<>
              <Button icon={ExternalLink} onClick={() => void lojaAbrir()}
                      disabledReason={loja.state !== 'online' ? 'Ligue a loja (Play Store) primeiro.' : null}>
                Abrir na Play Store</Button>
              <Button icon={DownloadCloud} onClick={() => void lojaCopiar()}
                      disabledReason={loja.state !== 'online' ? 'Ligue a loja (Play Store) primeiro.' : null}>
                Copiar da loja</Button>
              <span className={styles.muted}>
                {loja.store_version_name ? `A loja tem ${loja.store_version_name}` : 'A loja ainda não tem este app'}
                {loja.update_available ? ' — mais nova que o catálogo: copie para cá.' : '.'}
              </span>
            </>) : <span className={styles.muted}>Sem aparelho-loja configurado: só o envio de arquivo.</span>}
          </div>
        </CardBody>
      </Card>

      {!promovida ? (
        <Banner tone="warning" icon={TrendingUp} title="Sem versão promovida: ainda não há o que distribuir"
                actions={<Button size="sm" variant="primary" icon={TrendingUp}
                                 onClick={() => document.getElementById('loja-versoes')?.scrollIntoView({ block: 'start' })}>
                  Ir para as versões</Button>}>
          Envie ou copie uma versão, prove-a em um aparelho (canário) e depois use Promover. Só a versão promovida é
          distribuída.
        </Banner>
      ) : null}

      {promovida && atrasados.length > 0 ? (
        <Banner tone="info" icon={TrendingUp} title={`Atualização disponível para ${atrasados.length} aparelho(s)`}
                actions={<Button variant="primary" size="sm" icon={Send}
                                 onClick={() => setDistribuir({ release: promovida, pre: atrasados })}>
                  Atualizar para {promovida.version_name}</Button>}>
          {atrasados.join(', ')} estão numa versão anterior à promovida.
        </Banner>
      ) : null}

      <Card id="loja-versoes">
        <CardHeader title="Versões"
                    subtitle="Aprovar a assinatura → provar num aparelho (canário) → promover → distribuir. Só versão promovida é distribuída." />
        <CardBody>
          {releases.length === 0 ? <p className={styles.muted}>Nenhuma versão ainda. Envie o APK ou copie da loja.</p> : (
            <ul className={styles.versions}>
              {[...releases].sort((a, b) => b.version_code - a.version_code).map((r) => {
                const canal = CANAL[r.channel];
                const status = STATUS[r.status] ?? { rotulo: r.status, tom: 'neutral' as const };
                const nela = aparelhos.filter((a) => estadoDe(a.id)?.installed_release_id === r.id).length;
                const provavel = r.status === 'installable' && r.channel !== 'promoted' && r.channel !== 'canary';
                return (
                  <li key={r.id} className={styles.version}>
                    <span className={styles.versionName}>{r.version_name}</span>
                    <span className={styles.muted}>({r.version_code})</span>
                    <Badge size="sm" tone={canal.tom}>{canal.rotulo}</Badge>
                    <Badge size="sm" tone={status.tom}>{status.rotulo}</Badge>
                    <Badge size="sm" tone="muted">{ORIGEM[r.source_type]?.rotulo ?? r.source_type}</Badge>
                    <span className={styles.muted}>{nela} aparelho(s) nela</span>
                    <span className={styles.grow} />
                    {r.status === 'validated' ? (
                      <Button size="sm" icon={ShieldCheck} onClick={() => void aprovar(r)}>Aprovar assinatura</Button>) : null}
                    {provavel ? (<>
                      <Select small className={styles.inlineSelect} aria-label={`Aparelho da prova de ${r.version_name}`} value={provaEm[r.id] ?? ''}
                              onChange={(e) => setProvaEm({ ...provaEm, [r.id]: e.target.value })}>
                        <option value="">aparelho ligado</option>
                        {aparelhos.map((a) => <option key={a.id} value={a.id}>{a.id} · {a.state}</option>)}
                      </Select>
                      <Button size="sm" icon={FlaskConical} onClick={() => void canario(r)}>Provar</Button>
                    </>) : null}
                    {r.channel === 'canary' ? (
                      <Button size="sm" variant="primary" icon={TrendingUp}
                              onClick={() => void ciclo(r, { verb: 'promote' }, 'Versão promovida')}>Promover</Button>) : null}
                    {r.id === promovida?.id ? (
                      <Button size="sm" variant="primary" icon={Send} onClick={() => setDistribuir({ release: r })}>
                        Distribuir…</Button>) : null}
                    {r.channel === 'canary' || r.channel === 'promoted' ? (
                      <Button size="sm" variant="dangerGhost" icon={ShieldX} onClick={() => void quarentena(r)}>
                        Quarentena</Button>) : null}
                    {r.detail || r.channel_detail ? (
                      <span className={styles.versionDetail}>{r.channel_detail ?? r.detail}</span>) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Aparelhos"
                    subtitle="Estado lido do aparelho, ao vivo. Selecione para distribuir a versão promovida ou voltar a anterior."
                    actions={<Button size="sm" variant="ghost" icon={RefreshCw} onClick={() => void carregar()}>Recarregar</Button>} />
        <CardBody>
          <div className={styles.bulk}>
            <span className={styles.muted}>{selecionados.size} selecionado(s)</span>
            <Button size="sm" icon={Send} disabledReason={!promovida ? 'Não há versão promovida para distribuir.'
              : selecionados.size === 0 ? 'Selecione aparelhos na tabela.' : null}
                    onClick={() => promovida && setDistribuir({ release: promovida, pre: [...selecionados] })}>
              Distribuir {promovida?.version_name ?? ''} nos selecionados</Button>
            <Button size="sm" icon={Undo2} disabledReason={selecionados.size === 0 ? 'Selecione aparelhos na tabela.' : null}
                    onClick={() => void voltarSelecionados()}>Voltar versão nos selecionados</Button>
          </div>
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th><Checkbox aria-label="Selecionar todos" checked={todosSelecionados}
                                indeterminate={selecionados.size > 0 && !todosSelecionados}
                                onChange={(e) => setSelecionados(e.target.checked ? new Set(aparelhos.map((a) => a.id)) : new Set())} /></th>
                  <th>Aparelho</th><th>Versão</th><th>Situação</th><th>Leitura</th><th />
                </tr>
              </thead>
              <tbody>
                {aparelhos.map((a) => {
                  const e = estadoDe(a.id);
                  const pedida = e?.desired_release_id && e.desired_release_id !== e.installed_release_id
                    ? porId.get(e.desired_release_id) : null;
                  const andamento = e ? (ANDAMENTO[e.state] ?? { rotulo: e.state, tom: 'neutral' as const }) : null;
                  const entrega = ultimaEntrega[a.id];
                  const leitura = e ? idadeDaLeitura(e.verified_at) : null;
                  return (
                    <tr key={a.id}>
                      <td><Checkbox aria-label={`Selecionar ${a.id}`} checked={selecionados.has(a.id)}
                                    onChange={(ev) => {
                                      const novo = new Set(selecionados);
                                      if (ev.target.checked) novo.add(a.id); else novo.delete(a.id);
                                      setSelecionados(novo);
                                    }} /></td>
                      <td><strong>{a.id}</strong> <span className={styles.muted}>{a.state}{a.worker_id ? ` · ${a.worker_id}` : ''}</span></td>
                      <td>
                        {e?.installed_release_id ? porId.get(e.installed_release_id)?.version_name ?? e.observed_version_name
                          : e?.observed_version_name ?? <span className={styles.muted}>sem o app</span>}
                        {pedida ? <div className={styles.muted}>→ {pedida.version_name} pedida</div> : null}
                      </td>
                      <td>
                        {andamento ? <Badge size="sm" tone={andamento.tom}>{andamento.rotulo}</Badge> : null}
                        {entrega && !e ? <Badge size="sm" tone="neutral">{entrega.outcome}</Badge> : null}
                        {e?.detail ? <div className={styles.muted}>{e.detail}</div>
                          : entrega && !e ? <div className={styles.muted}>{entrega.reason}</div> : null}
                      </td>
                      <td>{leitura ? <Badge size="sm" tone={leitura.tom}>{leitura.rotulo}</Badge> : null}</td>
                      <td>
                        {e ? <Button size="sm" variant="ghost" icon={RefreshCw} iconOnly label={`Reler ${a.id}`}
                                     disabledReason={a.state !== 'online' ? 'Aparelho desligado.' : null}
                                     onClick={() => void verificar(a.id)} /> : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </CardBody>
      </Card>

      <DistribuirDialog
        release={distribuir?.release ?? null}
        nome={entry.name}
        preSelecao={distribuir?.pre}
        onClose={() => setDistribuir(null)}
        onDone={(devices) => {
          setUltimaEntrega(Object.fromEntries(devices.map((d) => [d.id, d])));
          void carregar();
          onChanged();
        }} />
    </div>
  );
}
