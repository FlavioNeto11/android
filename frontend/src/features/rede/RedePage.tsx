/**
 * Painel Rede (ADR-056, item 25.8): VPN e proxy por aparelho, desejado × observado, IP de saída medido e prova de
 * tráfego. É a aba principal no lugar de "Proxy" (ADR-056) — o proxy HTTP global legado (migração 041) continua
 * funcionando e some como uma linha desta tabela, no máximo "Configurado" (só prova a configuração, nunca o
 * tráfego, ADR-056 §2); para TIRAR um proxy legado ainda aplicado, a aba "Proxy (legado)" continua existindo à
 * parte — este painel só LÊ o legado, nunca escreve nele.
 *
 * Cada linha de `GET /api/network/devices` já vem pronta do backend (`rede.listar_aparelhos`, frente D1): o
 * aparelho, o desejado × observado novo, o legado, a quarentena e a CONTA REAL vinculada (`real_account`). Nada
 * disso é recombinado aqui — a versão anterior deste painel juntava `listInstances`/`listProxies`/`listProfiles`
 * (Instagram) no cliente e tinha uma heurística de conta real que ficava silenciosamente vazia quando a chamada de
 * personas falhava (achado do revisor no 25.8): com o backend já resolvendo tudo numa chamada só, essa classe de
 * bug não existe mais.
 */
import { Eye, KeyRound, Plus, RefreshCw, RotateCw, Send, ShieldAlert, ShieldCheck, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../../api/client';
import type {
  NetworkAssignDevice, NetworkDeviceRow, NetworkPolicy, NetworkProfileKind, NetworkProfileListed, NetworkProtocol,
} from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { Checkbox, Select, TextInput } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { type LoadError, LoadErrorState, toLoadError } from '../../lib/loadError';
import { useIntervaloVisivel } from '../../lib/polling';
import { metaOf, NETWORK_STATE, type Tone } from '../../lib/status';
import { toast, toastError } from '../../store/toasts';
import lojaStyles from '../loja/Loja.module.css';
import styles from './Rede.module.css';

const s = { ...lojaStyles, ...styles }; // classes das duas folhas, sem redeclarar o layout comum
const SEM_PERFIL = '__sem__';
const NENHUM = '(nenhum)';
const NAO_MUDAR = '';

const PROTOCOLOS: Record<NetworkProfileKind, NetworkProtocol[]> = {
  vpn: ['wireguard', 'singbox'],
  proxy: ['http', 'socks5'],
};

const POLITICA_LABEL: Record<NetworkPolicy, string> = {
  livre: 'Livre (sem exigência)',
  exigida: 'Exigida (só roda tarefa com tráfego verificado)',
  exigida_com_bloqueio: 'Exigida com bloqueio (idem, e o aparelho bloqueia fora da VPN)',
};

/** Os quatro desfechos de `rede._Item.como_dict()` — nunca o vocabulário do `DistributeDevice` (apps/proxy legado):
 *  a atribuição em lote grava direto (sem fila "aplicando"), e `refused` é o único jeito de recusa aqui. */
const PREVIA: Record<NetworkAssignDevice['outcome'], { rotulo: string; tom: Tone }> = {
  would_assign: { rotulo: 'aplicaria agora (prévia)', tom: 'info' },
  assigned: { rotulo: 'pedido: volta a pendente', tom: 'success' },
  unchanged: { rotulo: 'já é o pedido', tom: 'neutral' },
  refused: { rotulo: 'recusado', tom: 'danger' },
};

/** IPs medidos que aparecem em mais de um aparelho — ADR-056 §1: "dois aparelhos com o mesmo IP geram aviso". */
function ipsDuplicados(aparelhos: NetworkDeviceRow[]): Set<string> {
  const contagem = new Map<string, number>();
  for (const a of aparelhos) {
    const ip = a.network?.egress_ipv4;
    if (ip) contagem.set(ip, (contagem.get(ip) ?? 0) + 1);
  }
  return new Set([...contagem].filter(([, n]) => n > 1).map(([ip]) => ip));
}

export function RedePage() {
  const [perfis, setPerfis] = useState<NetworkProfileListed[] | null>(null);
  const [aparelhos, setAparelhos] = useState<NetworkDeviceRow[] | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [temDados, setTemDados] = useState(false);
  const [ocupadoPorId, setOcupadoPorId] = useState<Record<string, 'verify' | 'reapply' | undefined>>({});

  const carregar = useCallback(async () => {
    try {
      // Uma chamada só por lista: o backend (`rede.listar_perfis`/`listar_aparelhos`) já junta o legado, a
      // quarentena e a conta real. Nenhuma chamada de apoio que possa falhar em silêncio (achado do revisor).
      const [ps, ds] = await Promise.all([api.listNetworkProfiles(), api.listNetworkDevices()]);
      setPerfis(ps.profiles);
      setAparelhos(ds.devices);
      setErro(null);
      setTemDados(true);
    } catch (e) {
      setErro(toLoadError(e));
      if (temDados) toastError('Não foi possível recarregar a rede', e, { key: 'rede-carregar' });
    }
  }, [temDados]);

  useEffect(() => { void carregar(); }, [carregar]);
  const emAndamento = (aparelhos ?? []).some((a) => a.pending !== null);
  useIntervaloVisivel(carregar, 4000, emAndamento);

  const dupeIps = useMemo(() => ipsDuplicados(aparelhos ?? []), [aparelhos]);

  async function verificar(instanceId: string) {
    setOcupadoPorId((m) => ({ ...m, [instanceId]: 'verify' }));
    try {
      // 202 = pedido REGISTRADO, não medido: a sonda (25.5) ainda não roda. Nunca tratar isso como resultado da
      // medição (achado do revisor: "conta a incerteza como sucesso") — só relê a lista e mostra o motivo cru.
      const r = await api.verifyNetworkDevice(instanceId);
      toast({ tone: 'info', title: `${instanceId}: verificação pedida`, message: r.reason });
      await carregar();
    } catch (e) {
      toastError(`Não foi possível pedir a verificação de ${instanceId}`, e);
    } finally {
      setOcupadoPorId((m) => ({ ...m, [instanceId]: undefined }));
    }
  }

  async function reaplicar(instanceId: string) {
    setOcupadoPorId((m) => ({ ...m, [instanceId]: 'reapply' }));
    try {
      const r = await api.reapplyNetworkDevice(instanceId);
      toast({ tone: 'info', title: `${instanceId}: reaplicação pedida`, message: r.reason });
      await carregar();
    } catch (e) {
      toastError(`Não foi possível pedir a reaplicação em ${instanceId}`, e);
    } finally {
      setOcupadoPorId((m) => ({ ...m, [instanceId]: undefined }));
    }
  }

  async function apagarPerfil(id: string, nome: string) {
    const perfil = (perfis ?? []).find((p) => p.id === id);
    const emUso = (perfil?.in_use.length ?? 0) > 0;
    const { confirmed } = await confirm({
      title: `Apagar o perfil ${nome}?`, danger: true, confirmLabel: 'Apagar', cancelLabel: 'Cancelar',
      body: emUso
        ? `Este perfil está pedido para ${perfil!.in_use.join(', ')}: troque ou tire-o desses aparelhos antes (o backend recusa com 409).`
        : 'Só apaga o cadastro. O segredo (se houver) sai do cofre junto.',
    });
    if (!confirmed) return;
    try {
      await api.deleteNetworkProfile(id);
      await carregar();
    } catch (e) {
      toastError('Não foi possível apagar o perfil', e);
    }
  }

  if (!perfis || !aparelhos) {
    return erro
      ? <LoadErrorState what="a rede dos aparelhos" error={erro} onRetry={() => void carregar()} />
      : <LoadingRegion label="Carregando a rede…"><Skeleton height={280} /></LoadingRegion>;
  }

  return (
    <div className={s.stack}>
      <Banner tone="info" icon={ShieldCheck} compact title="O que cada estado prova">
        <strong>Configurado</strong> prova que o aparelho aceitou a configuração. <strong>Conectado</strong> prova
        que o cliente de VPN tem sessão ativa. Só <strong>tráfego verificado</strong> prova o IP de saída — medido
        de dentro do aparelho. "Testar" e "Reaplicar" só REGISTRAM o pedido (202): a aplicação e a medição de
        verdade são do item 25.4/25.5, que ainda não rodam. Nenhuma cor promete mais do que isso (ADR-056 §3).
      </Banner>

      <PerfisCard perfis={perfis} onCriado={carregar} onApagar={apagarPerfil} />

      <AtribuirCard perfis={perfis} aparelhos={aparelhos} onFeito={carregar} />

      <Card>
        <CardHeader title="Aparelhos" subtitle="Desejado × observado, IP de saída medido e a última verificação de cada aparelho."
                    actions={<Button size="sm" variant="ghost" icon={RefreshCw} onClick={() => void carregar()}>Recarregar</Button>} />
        <CardBody>
          <div className={s.tableWrap}>
            <table className={s.table}>
              <thead>
                <tr>
                  <th>Aparelho</th><th>VPN</th><th>Proxy</th><th>Política</th><th>Estado</th>
                  <th>IP de saída</th><th>Última verificação</th><th>Erro / pendência</th><th>Ações</th>
                </tr>
              </thead>
              <tbody>
                {aparelhos.map((row) => (
                  <LinhaAparelho key={row.instance_id} row={row} perfis={perfis} dupeIps={dupeIps}
                                 ocupado={ocupadoPorId[row.instance_id]}
                                 onVerificar={() => void verificar(row.instance_id)}
                                 onReaplicar={() => void reaplicar(row.instance_id)} />
                ))}
              </tbody>
            </table>
          </div>
          <p className={s.legend}>
            <span><ShieldAlert size={12} aria-hidden /> aviso de IP repetido</span>
            <span><KeyRound size={12} aria-hidden /> aparelho com conta real vinculada</span>
          </p>
        </CardBody>
      </Card>
    </div>
  );
}

function LinhaAparelho({ row, perfis, dupeIps, ocupado, onVerificar, onReaplicar }: {
  row: NetworkDeviceRow; perfis: NetworkProfileListed[]; dupeIps: Set<string>;
  ocupado: 'verify' | 'reapply' | undefined; onVerificar: () => void; onReaplicar: () => void;
}) {
  const { network: d, legacy_proxy: legado } = row;
  const nomeDe = (id: string | null) => (id ? perfis.find((p) => p.id === id)?.name ?? id : '—');
  const meta = row.effective_state ? metaOf(NETWORK_STATE, row.effective_state) : null;
  const somenteLegado = !d && !!legado;
  const dupe = !!d?.egress_ipv4 && dupeIps.has(d.egress_ipv4);
  return (
    <tr>
      <td>
        <strong>{row.instance_id}</strong> <span className={s.muted}>{row.device_state}{row.worker_id ? ` · ${row.worker_id}` : ''}</span>
        {row.real_account ? (
          <span title={`Conta real vinculada: ${row.real_account}. Mudar de saída exige autorização do dono por aparelho (ADR-056 §7).`}>
            <KeyRound size={12} aria-hidden />
          </span>
        ) : null}
      </td>
      <td>{nomeDe(d?.vpn_profile_id ?? null)}</td>
      <td>{d ? nomeDe(d.proxy_profile_id) : legado?.proxy_id ? `${legado.value} (legado)` : '—'}</td>
      <td>{d ? POLITICA_LABEL[d.policy].split(' (')[0] : '—'}</td>
      <td>
        {meta ? <StatusBadge meta={somenteLegado ? { ...meta, description: 'Proxy legado (migração 041): prova a configuração, não o tráfego.' } : meta} size="sm" />
              : <span className={s.muted}>sem rede pedida</span>}
        {row.pending === 'aplicar' ? <div className={s.rowNote}>aguardando aplicação no aparelho (25.4)</div> : null}
        {row.pending === 'verificar' ? <div className={s.rowNote}>aguardando verificação (25.5)</div> : null}
      </td>
      <td>
        {d?.egress_ipv4 ? (
          <>
            <code className={dupe ? s.dupe : undefined}>{d.egress_ipv4}</code>
            {dupe ? <span title="Outro aparelho mediu o mesmo IP agora."><ShieldAlert size={12} aria-hidden /></span> : null}
            {row.last_measurement ? <div className={s.rowNote}>método: {row.last_measurement.method}</div> : null}
          </>
        ) : <span className={s.muted}>não medido</span>}
      </td>
      <td>{d?.verified_at ? new Date(d.verified_at).toLocaleString('pt-BR') : <span className={s.muted}>nunca</span>}</td>
      <td>
        {row.restriction ? <span className={s.dupe}>{row.restriction}</span>
          : d?.error ? <span className={s.dupe}>{d.error}</span>
          : d?.detail ?? <span className={s.muted}>—</span>}
      </td>
      <td>
        <div className={s.actions}>
          <Button size="sm" variant="ghost" icon={ShieldCheck} loading={ocupado === 'verify'}
                  disabledReason={!d || (!d.vpn_profile_id && !d.proxy_profile_id) ? 'Atribua um perfil antes de testar.' : null}
                  onClick={onVerificar}>Testar</Button>
          <Button size="sm" variant="ghost" icon={RotateCw} loading={ocupado === 'reapply'}
                  disabledReason={!d ? 'Atribua um perfil antes de reaplicar.' : null}
                  onClick={onReaplicar}>Reaplicar</Button>
        </div>
      </td>
    </tr>
  );
}

function PerfisCard({ perfis, onCriado, onApagar }: {
  perfis: NetworkProfileListed[]; onCriado: () => Promise<void>; onApagar: (id: string, nome: string) => Promise<void>;
}) {
  const [nome, setNome] = useState('');
  const [kind, setKind] = useState<NetworkProfileKind>('vpn');
  const [protocol, setProtocol] = useState<NetworkProtocol>('wireguard');
  const [host, setHost] = useState('');
  const [porta, setPorta] = useState('51820');
  const [secret, setSecret] = useState('');
  const [ocupado, setOcupado] = useState(false);

  function mudarKind(k: NetworkProfileKind) {
    setKind(k);
    setProtocol(PROTOCOLOS[k][0]!);
  }

  async function criar() {
    setOcupado(true);
    try {
      await api.createNetworkProfile({
        name: nome.trim(), kind, protocol, endpoint_host: host.trim(), endpoint_port: Number(porta),
        secret: secret || undefined,
      });
      toast({ tone: 'success', title: `Perfil ${nome.trim()} criado` });
      setNome(''); setHost(''); setSecret('');
      await onCriado();
    } catch (e) {
      toastError('Não foi possível criar o perfil', e);
    } finally {
      setOcupado(false);
    }
  }

  const faltando = !nome.trim() || !host.trim() || !Number(porta) ? 'Preencha nome, host e porta.' : null;

  return (
    <Card>
      <CardHeader title="Perfis de VPN e proxy" subtitle="O segredo (chave, senha ou certificado) entra uma vez, vai para o cofre e nunca é mostrado de novo." />
      <CardBody>
        <div className={s.secretRow}>
          <TextInput aria-label="Nome do perfil" placeholder="Nome (ex.: WireGuard escritório)" value={nome} maxLength={60}
                     onChange={(e) => setNome(e.target.value)} />
          <Select aria-label="Tipo" small value={kind} onChange={(e) => mudarKind(e.target.value as NetworkProfileKind)}>
            <option value="vpn">VPN</option>
            <option value="proxy">Proxy</option>
          </Select>
          <Select aria-label="Protocolo" small value={protocol} onChange={(e) => setProtocol(e.target.value as NetworkProtocol)}>
            {PROTOCOLOS[kind].map((p) => <option key={p} value={p}>{p}</option>)}
          </Select>
          <TextInput aria-label="Host" placeholder="Host ou IP" value={host} mono maxLength={253} onChange={(e) => setHost(e.target.value)} />
          <TextInput aria-label="Porta" type="number" min={1} max={65535} value={porta} onChange={(e) => setPorta(e.target.value)} />
          <Button icon={Plus} loading={ocupado} disabledReason={faltando} onClick={() => void criar()}>Criar</Button>
        </div>
        <div className={s.secretRow} style={{ marginTop: 'var(--sp-2)' }}>
          <TextInput aria-label="Segredo (chave, senha ou certificado)" type="password" placeholder="Segredo — opcional, nunca reexibido"
                     autoComplete="new-password" value={secret} onChange={(e) => setSecret(e.target.value)}
                     style={{ gridColumn: 'span 3' }} />
        </div>
        {perfis.length === 0 ? <p className={s.muted}>Nenhum perfil cadastrado.</p> : (
          <ul className={s.versions} style={{ marginTop: 'var(--sp-3)' }}>
            {perfis.map((p) => (
              <li key={p.id} className={s.version}>
                <span className={s.versionName}>{p.name}</span>
                <Badge size="sm" tone={p.kind === 'vpn' ? 'accent' : 'info'}>{p.kind} · {p.protocol}</Badge>
                <code>{p.endpoint_host}:{p.endpoint_port}</code>
                {p.has_secret ? <Badge size="sm" tone="neutral">segredo guardado</Badge> : null}
                <span className={s.grow} />
                <Button size="sm" variant="dangerGhost" icon={Trash2} iconOnly label={`Apagar ${p.name}`}
                        onClick={() => void onApagar(p.id, p.name)} />
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

function AtribuirCard({ perfis, aparelhos, onFeito }: {
  perfis: NetworkProfileListed[]; aparelhos: NetworkDeviceRow[]; onFeito: () => Promise<void>;
}) {
  const [vpnId, setVpnId] = useState<string>('');
  const [proxyId, setProxyId] = useState<string>('');
  // '' = "não mudar" (padrão): só entra no pedido quando a pessoa mexe. O padrão antigo era 'livre' sempre
  // mandado, o que rebaixava silenciosamente um aparelho em política 'exigida' — achado do revisor no 25.8.
  const [policy, setPolicy] = useState<NetworkPolicy | typeof NAO_MUDAR>(NAO_MUDAR);
  const [selecionados, setSelecionados] = useState<Set<string>>(new Set());
  const [previa, setPrevia] = useState<NetworkAssignDevice[] | null>(null);
  const [ocupado, setOcupado] = useState(false);

  useEffect(() => setPrevia(null), [vpnId, proxyId, policy, selecionados]);

  const vpnOpts = perfis.filter((p) => p.kind === 'vpn');
  const proxyOpts = perfis.filter((p) => p.kind === 'proxy');
  const todos = aparelhos.length > 0 && selecionados.size === aparelhos.length;
  const semAlvo = !vpnId && !proxyId && !policy ? 'Escolha ao menos um perfil (ou "nenhum" para tirar) ou uma política.'
    : selecionados.size === 0 ? 'Selecione aparelhos.' : null;

  const corpo = (dryRun: boolean, confirmados: string[] = []) => ({
    instance_ids: [...selecionados],
    vpn_profile_id: vpnId === '' ? undefined : vpnId === SEM_PERFIL ? null : vpnId,
    proxy_profile_id: proxyId === '' ? undefined : proxyId === SEM_PERFIL ? null : proxyId,
    ...(policy === NAO_MUDAR ? {} : { policy }),
    confirm_real_account: confirmados,
    dry_run: dryRun,
  });

  async function verPrevia() {
    setOcupado(true);
    try {
      const r = await api.assignNetwork(corpo(true));
      setPrevia(r.devices);
    } catch (e) {
      toastError('A prévia foi recusada', e);
    } finally {
      setOcupado(false);
    }
  }

  async function aplicar() {
    if (!previa) return;
    // Só o backend sabe quem precisa de confirmação (409 `real_account_confirm_required` na prévia): nada de
    // heurística local. Um diálogo POR APARELHO (ADR-056 §7, T11) — recusar qualquer um aborta o lote inteiro,
    // porque `rede.atribuir` é tudo ou nada (um recusado devolve 409 e não grava nada).
    const precisamConfirmar = previa.filter((d) => d.code === 'real_account_confirm_required');
    const confirmados: string[] = [];
    for (const item of precisamConfirmar) {
      const conta = aparelhos.find((a) => a.instance_id === item.id)?.real_account ?? 'conta vinculada';
      const { confirmed } = await confirm({
        title: `Mudar a saída de ${item.id}?`, danger: true, confirmLabel: 'Mudar mesmo assim',
        cancelLabel: 'Cancelar', icon: ShieldAlert,
        body: `${item.id} tem conta real vinculada (${conta}). Trocar a saída de uma conta logada costuma disparar `
          + 'verificação e já custou contas antes (ADR-056 §7). Confirme só se o dono autorizou para ESTE aparelho.',
      });
      if (!confirmed) return;
      confirmados.push(item.id);
    }
    setOcupado(true);
    try {
      const r = await api.assignNetwork(corpo(false, confirmados));
      const n = (o: NetworkAssignDevice['outcome']) => r.devices.filter((x) => x.outcome === o).length;
      toast({ tone: 'info', title: 'Atribuição pedida',
              message: `${n('assigned')} pedido(s) (volta(m) a pendente até o aparelho confirmar) · ${n('unchanged')} já assim.` });
      setPrevia(null);
      setSelecionados(new Set());
      await onFeito();
    } catch (e) {
      toastError('O pedido foi recusado', e);
    } finally {
      setOcupado(false);
    }
  }

  return (
    <Card>
      <CardHeader title="Atribuir rede em lote" subtitle="Escolha os perfis e/ou a política, veja a prévia (obrigatória) e confirme. Um único aparelho marcado serve como edição individual." />
      <CardBody>
        <div className={s.bulk}>
          <Select aria-label="Perfil de VPN" small className={s.inlineSelect} value={vpnId} onChange={(e) => setVpnId(e.target.value)}>
            <option value="">VPN: não mudar</option>
            <option value={SEM_PERFIL}>{NENHUM} — tirar VPN</option>
            {vpnOpts.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
          <Select aria-label="Perfil de proxy" small className={s.inlineSelect} value={proxyId} onChange={(e) => setProxyId(e.target.value)}>
            <option value="">Proxy: não mudar</option>
            <option value={SEM_PERFIL}>{NENHUM} — tirar proxy</option>
            {proxyOpts.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
          <Select aria-label="Política" small className={s.inlineSelect} value={policy} onChange={(e) => setPolicy(e.target.value as NetworkPolicy | typeof NAO_MUDAR)}>
            <option value={NAO_MUDAR}>Política: não mudar</option>
            {(Object.keys(POLITICA_LABEL) as NetworkPolicy[]).map((p) => <option key={p} value={p}>{POLITICA_LABEL[p]}</option>)}
          </Select>
          <span className={s.muted}>{selecionados.size} selecionado(s)</span>
          <Button size="sm" icon={Eye} loading={ocupado && !previa} disabledReason={semAlvo} onClick={() => void verPrevia()}>Ver prévia</Button>
          <Button size="sm" variant="primary" icon={Send} loading={ocupado && !!previa}
                  disabledReason={semAlvo ?? (!previa ? 'Veja a prévia antes de confirmar.' : null)}
                  onClick={() => void aplicar()}>Aplicar</Button>
        </div>
        {previa ? (
          <ul className={s.preview} aria-label="Prévia da atribuição de rede">
            {previa.map((d) => (
              <li key={d.id}><strong>{d.id}</strong>
                <Badge size="sm" tone={PREVIA[d.outcome]?.tom ?? 'neutral'}>{PREVIA[d.outcome]?.rotulo ?? d.outcome}</Badge>
                <span className={s.muted}>{d.reason}</span>
                {d.warnings.map((w) => <span key={w} className={s.rowNote}>{w}</span>)}
              </li>
            ))}
          </ul>
        ) : null}
        <div className={s.tableWrap} style={{ marginTop: 'var(--sp-2)' }}>
          <table className={s.table}>
            <thead>
              <tr>
                <th><Checkbox aria-label="Selecionar todos" checked={todos}
                              indeterminate={selecionados.size > 0 && !todos}
                              onChange={(e) => setSelecionados(e.target.checked ? new Set(aparelhos.map((a) => a.instance_id)) : new Set())} /></th>
                <th>Aparelho</th><th>Conta real</th>
              </tr>
            </thead>
            <tbody>
              {aparelhos.map((a) => (
                <tr key={a.instance_id}>
                  <td><Checkbox aria-label={`Selecionar ${a.instance_id}`} checked={selecionados.has(a.instance_id)}
                                onChange={(e) => {
                                  const novo = new Set(selecionados);
                                  if (e.target.checked) novo.add(a.instance_id); else novo.delete(a.instance_id);
                                  setSelecionados(novo);
                                }} /></td>
                  <td>{a.instance_id}</td>
                  <td>{a.real_account ? <Badge size="sm" tone="warning" icon={KeyRound}>{a.real_account}</Badge> : <span className={s.muted}>—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardBody>
    </Card>
  );
}
