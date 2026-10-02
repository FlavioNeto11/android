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
import { Eye, Home, KeyRound, Plus, RefreshCw, RotateCw, Send, Server, ShieldAlert, ShieldCheck, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../api/client';
import type {
  DeviceNetwork, NetworkAssignDevice, NetworkAssignRequest, NetworkCentralEgress, NetworkDeviceRow, NetworkEgressHome, NetworkExpectedEgress, NetworkFirewallState,
  NetworkMeasurement, NetworkPolicy, NetworkProfileKind, NetworkProfileListed, NetworkProtocol, NetworkServerStatus,
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

/** Os IPs de saída medidos de um aparelho: o IPv4 e o IPv6 (um aparelho pode sair só por um deles). */
function ipsMedidos(a: NetworkDeviceRow): string[] {
  return [a.network?.egress_ipv4, a.network?.egress_ipv6].filter((ip): ip is string => !!ip);
}

/** IPs medidos que aparecem em mais de um aparelho — ADR-056 §1: "dois aparelhos com o mesmo IP geram aviso". Os
 *  dois protocolos contam: dois aparelhos que saem pelo mesmo IPv6 são o mesmo caso que pelo mesmo IPv4. */
function ipsDuplicados(aparelhos: NetworkDeviceRow[]): Set<string> {
  const contagem = new Map<string, number>();
  for (const a of aparelhos) {
    for (const ip of new Set(ipsMedidos(a))) contagem.set(ip, (contagem.get(ip) ?? 0) + 1);
  }
  return new Set([...contagem].filter(([, n]) => n > 1).map(([ip]) => ip));
}

export function RedePage() {
  const [perfis, setPerfis] = useState<NetworkProfileListed[] | null>(null);
  const [aparelhos, setAparelhos] = useState<NetworkDeviceRow[] | null>(null);
  const [central, setCentral] = useState<NetworkCentralEgress | null>(null);
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
      setCentral(ds.central_egress ?? null);
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
      // 202 = pedido REGISTRADO, não medido: a sonda (25.5) mede no próximo ponto seguro do aparelho. Nunca tratar
      // isso como resultado da medição (achado do revisor: "conta a incerteza como sucesso") — só relê a lista e
      // mostra o motivo cru; a medição chega depois em `last_measurement`.
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
        de dentro do aparelho, por app. "Testar" e "Reaplicar" só REGISTRAM o pedido (202): a convergência aplica
        e a sonda de saída mede no próximo ponto seguro do aparelho, e o resultado aparece nesta tabela. Nenhuma
        cor promete mais do que isso (ADR-056 §3).
      </Banner>

      <PerfisCard perfis={perfis} onCriado={carregar} onApagar={apagarPerfil} />

      <AtribuirCard perfis={perfis} aparelhos={aparelhos} onFeito={carregar} />

      <Card>
        <CardHeader title="Aparelhos" subtitle="Desejado × observado, IP de saída medido e a última verificação de cada aparelho."
                    actions={<Button size="sm" variant="ghost" icon={RefreshCw} onClick={() => void carregar()}>Recarregar</Button>} />
        <CardBody>
          <ResumoDaCasa aparelhos={aparelhos} central={central} />
          <div className={s.tableWrap}>
            <table className={`${s.table} ${s.devices}`}>
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

      <ServidorCard />
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
  // Aviso de saída repetida: a contagem local (IPv4 e IPv6 da lista que acabou de chegar) ou o backend
  // (`egress_shared_with`, que compara com todos os aparelhos).
  const ips = ipsMedidos(row);
  const dupe = ips.some((ip) => dupeIps.has(ip)) || row.egress_shared_with.length > 0;
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
      <td>
        {d ? nomeDe(d.proxy_profile_id)
          : legado?.proxy_id ? `${legado.value} (legado)`
          : legado ? (
            // Há linha do legado e nenhum proxy pedido: o aparelho foi LIDO e está sem proxy. Um "—" aqui se
            // confundia com "nunca conferido" (android-01, 30/09).
            <span className={s.muted} title={legado.detail ?? 'Proxy global do Android lido de volta: nenhum configurado.'}>
              sem proxy (legado conferido)
            </span>
          ) : <span className={s.muted} title="Nenhum proxy pedido nem lido para este aparelho.">—</span>}
      </td>
      <td>{d ? POLITICA_LABEL[d.policy].split(' (')[0] : '—'}</td>
      <td>
        {meta ? <StatusBadge meta={somenteLegado ? { ...meta, description: 'Proxy legado (migração 041): prova a configuração, não o tráfego.' } : meta} size="sm" />
              : <span className={s.muted}>sem rede pedida</span>}
        {row.pending === 'aplicar' ? <div className={s.rowNote}>aguardando aplicação no aparelho (25.4)</div> : null}
        {row.pending === 'verificar' ? <div className={s.rowNote}>aguardando a medição de dentro do aparelho</div> : null}
      </td>
      <td>
        {ips.length ? (
          <>
            {ips.map((ip) => <code key={ip} className={dupeIps.has(ip) ? s.dupe : undefined}>{ip}</code>)}
            {dupe ? <span title="Outro aparelho mediu o mesmo IP agora."><ShieldAlert size={12} aria-hidden /></span> : null}
            {row.egress_shared_with.length > 0 ? (
              <div className={s.rowNote}>mesma saída que {row.egress_shared_with.join(', ')}</div>
            ) : null}
          </>
        ) : <span className={s.muted}>não medido</span>}
        {row.egress_home ? <SaiPelaCasa home={row.egress_home} /> : null}
        {row.egress_expected ? <SaidaEsperada esperada={row.egress_expected} confere={row.egress_matches ?? null} /> : null}
        {row.last_measurement ? <ResumoDaMedicao m={row.last_measurement} /> : null}
        {d && d.policy === 'exigida_com_bloqueio' ? <ProvaDeVazamento d={d} /> : null}
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

/** As frases do que acusa em `egress_home`, na ordem de gravidade. Vazia = nada acusa. */
function acusacoesDaCasa(h: NetworkEgressHome): string[] {
  return [
    h.ipv4 ? 'IPv4 igual ao do central' : null,
    h.ipv6 ? 'IPv6 na mesma rede do central' : null,
    h.ipv6_outside_profile ? 'IPv6 fora do perfil' : null,
  ].filter((x): x is string => !!x);
}

/** "Sai pela casa" (29.20) ao lado do IP medido: o que ACUSA em destaque (com o motivo no `title`), "não sai" só com
 *  medida dos dois lados, e "sem medida" quando falta uma delas — nunca um "limpo" presumido. */
function SaiPelaCasa({ home }: { home: NetworkEgressHome }) {
  const acusa = acusacoesDaCasa(home);
  if (acusa.length > 0) {
    return (
      <div className={`${s.rowNote} ${s.dupe}`} title={home.reason}>
        <Home size={12} aria-hidden /> sai pela casa: {acusa.join(' · ')}
      </div>
    );
  }
  return (
    <div className={s.rowNote} title={home.reason}>
      {home.leaves_by_home === false ? 'não sai pela casa' : 'sai pela casa: sem medida'}
    </div>
  );
}

/** A linha de resumo acima da tabela: quantos aparelhos ainda saem pela casa, quantos estão sem medida, e a saída
 *  medida do central. Sem `egress_home` em nenhuma linha (backend de antes do 29.20), não mostra nada. */
function ResumoDaCasa({ aparelhos, central }: { aparelhos: NetworkDeviceRow[]; central: NetworkCentralEgress | null }) {
  const veredictos = aparelhos.map((a) => a.egress_home).filter((h): h is NetworkEgressHome => !!h);
  if (veredictos.length === 0) return null;
  const saem = aparelhos.filter((a) => a.egress_home?.leaves_by_home === true);
  const semMedida = veredictos.filter((h) => h.leaves_by_home === null).length;
  const saidaCentral = central ? [central.ipv4, central.ipv6].filter((ip): ip is string => !!ip).join(' e ') : '';
  return (
    <Banner tone={saem.length > 0 ? 'danger' : 'info'} icon={Home} compact title="Saída pela casa">
      <strong>{saem.length === 1 ? '1 aparelho ainda sai' : `${saem.length} aparelhos ainda saem`} pela casa</strong>
      {saem.length > 0 ? ` (${saem.map((a) => a.instance_id).join(', ')})` : ''} · {semMedida} sem medida.{' '}
      {saidaCentral
        ? <>Saída medida do central: <code>{saidaCentral}</code>.</>
        : <span title={central?.reason}>Saída do central ainda não medida{central ? ` (${central.reason})` : ''}.</span>}
    </Banner>
  );
}

/** A saída que o perfil declara (29.6) ao lado da medida. Três leituras, e nenhuma presumida: `confere` (a medida
 *  desta revisão é a esperada), "é outra" (o aparelho fica `parcial`; com política exigida a tarefa espera) e sem
 *  veredito (a revisão pedida ainda não foi medida). Saída compartilhada é outro aviso, e continua à parte. */
function SaidaEsperada({ esperada, confere }: { esperada: NetworkExpectedEgress; confere: boolean | null }) {
  const ips = [esperada.ipv4, esperada.ipv6].filter((ip): ip is string => !!ip).join(' e ');
  const difere = confere === false;
  return (
    <div className={difere ? `${s.rowNote} ${s.dupe}` : s.rowNote}
         title={difere ? `A saída medida não é a que o perfil ${esperada.profile_name} declara: o aparelho fica parcial até medir ${ips}.` : undefined}>
      esperada {ips} (perfil {esperada.profile_name}){difere ? ' — a saída medida é outra' : confere ? ' — confere' : ''}
    </div>
  );
}

const RESULTADO_POR_APP: Record<string, string> = {
  ok: 'pelo túnel', fora_da_rede: 'FORA da rede pedida', falhou: 'não conectou', nao_medido: 'sem tráfego medido',
};

function simNao(v: boolean | null, sim: string, nao: string): string {
  return v === null ? 'não medido' : v ? sim : nao;
}

const UDP_FALHOU = 'Uma perna de UDP ficou sem resposta em todos os datagramas desta medição. É aviso: UDP ainda não '
  + 'decide "tráfego verificado".';

/** A última medição da sonda de saída (25.5), como o backend a gravou: por app (o navegador não prova os outros
 *  apps), DNS, UDP e o teste de vazamento. `null` é "não medido" — nunca um "ok" presumido. */
function ResumoDaMedicao({ m }: { m: NetworkMeasurement }) {
  const apps = Object.entries(m.per_app);
  // As duas pernas de UDP (29.5): DNS por UDP e NTP. Vêm derivadas do `detail` pela listagem; sem elas (backend de
  // antes, ou `detail` que não diz), fica o "UDP ok/falhou" do `udp_ok`, como sempre foi.
  const dns = m.udp_dns_ok ?? null;
  const ntp = m.udp_ntp_ok ?? null;
  const porPerna = dns !== null || ntp !== null;
  return (
    <div className={s.rowNote} title={m.detail ?? undefined}>
      <div>método: {m.method}</div>
      <div>
        DNS {m.dns_resolver ?? 'não lido'}{porPerna ? '' : ` · UDP ${simNao(m.udp_ok, 'ok', 'falhou')}`} · vazamento{' '}
        {simNao(m.leak_blocked, 'bloqueado', 'NÃO bloqueado')}
      </div>
      {porPerna ? (
        // Destaque quando uma perna falha: é aviso (UDP ainda não decide "tráfego verificado"), mas não pode sumir.
        <div className={dns === false || ntp === false ? s.dupe : undefined}
             title={dns === false || ntp === false ? UDP_FALHOU : undefined}>
          UDP: DNS {simNao(dns, 'ok', 'falhou')} · NTP {simNao(ntp, 'ok', 'falhou')}
        </div>
      ) : null}
      {apps.map(([pkg, r]) => (
        <div key={pkg} className={r === 'ok' ? undefined : s.dupe}>
          {pkg}: {RESULTADO_POR_APP[String(r)] ?? String(r)}
        </div>
      ))}
    </div>
  );
}

/** A prova do teste de vazamento que a LINHA guarda (29.2): é ela, e não o "vazamento" da última medição, que
 *  decide a política com bloqueio. Presa à revisão e à instalação do cliente VPN; sobrevive a reinício do backend. */
function ProvaDeVazamento({ d }: { d: DeviceNetwork }) {
  const daRevisao = d.leak_rev !== null && d.leak_rev === d.desired_rev;
  const quando = d.leak_at ? new Date(d.leak_at).toLocaleString('pt-BR') : 'sem data';
  const cliente = d.leak_client ? d.leak_client.split(' /')[0] : 'cliente não lido';
  let texto: string;
  let ruim = true;
  if (d.leak_pending) {
    texto = 'bloqueio fora da VPN: teste em curso (o cliente VPN é parado para a sonda)';
  } else if (!daRevisao) {
    texto = d.leak_rev === null ? 'bloqueio fora da VPN: sem prova (nenhum teste para esta revisão)'
      : `bloqueio fora da VPN: a prova é da rev ${d.leak_rev}, e a pedida é a ${d.desired_rev}`;
  } else if (d.leak_result === true) {
    texto = `bloqueio fora da VPN: provado em ${quando} (rev ${d.leak_rev}, cliente ${cliente})`;
    ruim = false;
  } else if (d.leak_result === false) {
    texto = `bloqueio fora da VPN: VAZOU no teste de ${quando} — "Testar" refaz`;
  } else {
    texto = `bloqueio fora da VPN: teste de ${quando} não concluiu — "Testar" refaz (reinicia o aparelho)`;
  }
  return <div className={ruim ? `${s.rowNote} ${s.dupe}` : s.rowNote} title={d.leak_detail ?? undefined}>{texto}</div>;
}

/** A saída que o perfil declara em `params` (29.6), como texto; `''` = não declara. */
function saidaDeclarada(p: NetworkProfileListed): string {
  return [p.params.egress_esperado, p.params.egress_esperado_ipv6]
    .filter((ip): ip is string => typeof ip === 'string' && ip.length > 0).join(' e ');
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
  // A saída que o perfil deve dar (29.6): opcional, e só com ela a medição confere a saída do aparelho. Quem valida
  // é o backend (IPv4 público, a mesma regra da medição): o 422 dele aparece no aviso de erro.
  const [saida, setSaida] = useState('');
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
        ...(saida.trim() ? { params: { egress_esperado: saida.trim() } } : {}),
      });
      toast({ tone: 'success', title: `Perfil ${nome.trim()} criado` });
      setNome(''); setHost(''); setSecret(''); setSaida('');
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
          <TextInput aria-label="Saída esperada (IPv4 público, opcional)" placeholder="Saída esperada — IPv4 público, opcional"
                     title="O IP público pelo qual o aparelho deve sair com este perfil. Com ele, a medição confere a saída; sem ele, só avisa de saída repetida."
                     value={saida} mono maxLength={45} onChange={(e) => setSaida(e.target.value)}
                     style={{ gridColumn: 'span 2' }} />
        </div>
        {perfis.length === 0 ? <p className={s.muted}>Nenhum perfil cadastrado.</p> : (
          <ul className={s.versions} style={{ marginTop: 'var(--sp-3)' }}>
            {perfis.map((p) => (
              <li key={p.id} className={s.version}>
                <span className={s.versionName}>{p.name}</span>
                <Badge size="sm" tone={p.kind === 'vpn' ? 'accent' : 'info'}>{p.kind} · {p.protocol}</Badge>
                <code>{p.endpoint_host}:{p.endpoint_port}</code>
                {p.has_secret ? <Badge size="sm" tone="neutral">segredo guardado</Badge> : null}
                {saidaDeclarada(p) ? <Badge size="sm" tone="info">saída esperada {saidaDeclarada(p)}</Badge> : null}
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
  // A prévia guarda o PEDIDO que ela mostrou: "Aplicar" manda exatamente esse, nunca a seleção de agora.
  const [previa, setPrevia] = useState<{ devices: NetworkAssignDevice[]; pedido: NetworkAssignRequest } | null>(null);
  const [ocupado, setOcupado] = useState(false);
  // Cada mudança de seleção invalida a prévia EM VOO também: a resposta que chega depois da mudança é de uma seleção
  // que ninguém está vendo, e religaria "Aplicar" para mandar a seleção nova sem prévia (como a Loja, que confirma
  // os MESMOS aparelhos da prévia).
  const versaoDaSelecao = useRef(0);

  useEffect(() => {
    versaoDaSelecao.current += 1;
    setPrevia(null);
  }, [vpnId, proxyId, policy, selecionados]);

  const vpnOpts = perfis.filter((p) => p.kind === 'vpn');
  const proxyOpts = perfis.filter((p) => p.kind === 'proxy');
  const todos = aparelhos.length > 0 && selecionados.size === aparelhos.length;
  const semAlvo = !vpnId && !proxyId && !policy ? 'Escolha ao menos um perfil (ou "nenhum" para tirar) ou uma política.'
    : selecionados.size === 0 ? 'Selecione aparelhos.' : null;

  const corpo = (dryRun: boolean, confirmados: string[] = []): NetworkAssignRequest => ({
    instance_ids: [...selecionados],
    vpn_profile_id: vpnId === '' ? undefined : vpnId === SEM_PERFIL ? null : vpnId,
    proxy_profile_id: proxyId === '' ? undefined : proxyId === SEM_PERFIL ? null : proxyId,
    ...(policy === NAO_MUDAR ? {} : { policy }),
    confirm_real_account: confirmados,
    dry_run: dryRun,
  });

  async function verPrevia() {
    const versao = versaoDaSelecao.current;
    const pedido = corpo(true);
    setOcupado(true);
    try {
      const r = await api.assignNetwork(pedido);
      if (versao !== versaoDaSelecao.current) return;      // a seleção mudou durante o voo: prévia de outra seleção
      setPrevia({ devices: r.devices, pedido });
    } catch (e) {
      if (versao !== versaoDaSelecao.current) return;
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
    const precisamConfirmar = previa.devices.filter((d) => d.code === 'real_account_confirm_required');
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
      const r = await api.assignNetwork({ ...previa.pedido, confirm_real_account: confirmados, dry_run: false });
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
            {previa.devices.map((d) => (
              <li key={d.id}><strong>{d.id}</strong>
                <Badge size="sm" tone={PREVIA[d.outcome]?.tom ?? 'neutral'}>{PREVIA[d.outcome]?.rotulo ?? d.outcome}</Badge>
                <span className={s.muted}>{d.reason}</span>
                {d.warnings.map((w) => <span key={w} className={s.rowNote}>{w}</span>)}
                {/* Avisos de saída (29.6): não recusam, mas são a troca de IP que a pessoa precisa ler antes de aplicar. */}
                {(d.egress_warnings ?? []).map((w) => (
                  <span key={w.code} data-aviso-de-saida={w.code} className={`${s.rowNote} ${s.dupe}`}>
                    <ShieldAlert size={12} aria-hidden /> {w.message}
                  </span>
                ))}
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

const FIREWALL: Record<NetworkFirewallState, { rotulo: string; tom: Tone }> = {
  liberado: { rotulo: 'firewall liberado', tom: 'success' },
  desligado: { rotulo: 'firewall desligado', tom: 'warning' },
  bloqueado: { rotulo: 'firewall bloqueia', tom: 'danger' },
  sem_regra: { rotulo: 'firewall sem regra', tom: 'danger' },
  regra_obsoleta: { rotulo: 'regra do firewall obsoleta', tom: 'danger' },
  desconhecido: { rotulo: 'firewall não lido', tom: 'neutral' },
};

/** O servidor do central e o caminho dos aparelhos de OUTRA máquina até ele (25.7). Carrega à parte da tabela: uma
 *  falha aqui não esconde os aparelhos. O firewall é só LIDO — o comando é do dono, nunca executado pelo painel. */
function ServidorCard() {
  const [status, setStatus] = useState<NetworkServerStatus | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [conferindo, setConferindo] = useState(false);

  const carregar = useCallback(async () => {
    try {
      setStatus(await api.getNetworkServer());
      setErro(null);
    } catch (e) {
      setErro(toLoadError(e).message);
    }
  }, []);

  useEffect(() => { void carregar(); }, [carregar]);

  async function conferirFirewall() {
    setConferindo(true);
    try {
      const acesso = await api.checkNetworkServerFirewall();
      setStatus((atual) => (atual ? { ...atual, remote_access: acesso } : atual));
      if (acesso.firewall) toast({ tone: 'info', title: FIREWALL[acesso.firewall.state].rotulo, message: acesso.firewall.detail });
    } catch (e) {
      toastError('Não foi possível ler o firewall do central', e);
    } finally {
      setConferindo(false);
    }
  }

  const acesso = status?.remote_access;
  const fw = acesso?.firewall ?? null;
  return (
    <Card>
      <CardHeader title="Servidor do central" subtitle="O sing-box em modo usuário e o caminho dos aparelhos de outra máquina (worker da LAN) até ele."
                  actions={<Button size="sm" variant="ghost" icon={ShieldCheck} loading={conferindo} onClick={() => void conferirFirewall()}>Conferir firewall</Button>} />
      <CardBody>
        {erro ? <p className={s.legend}>Servidor: {erro}</p> : null}
        {status ? (
          <div className={s.stack}>
            <p className={s.legend}>
              <span><Server size={12} aria-hidden /> {status.running ? `no ar (${status.peers.length} par(es))` : 'parado'}</span>
              <span>WireGuard UDP {status.wireguard_udp_port}, túnel {status.subnet}</span>
              <span>aparelhos de outra máquina: {acesso?.remote_peers.length ? acesso.remote_peers.join(', ') : 'nenhum'}</span>
            </p>
            <p className={s.legend}>
              <span>Endereço na LAN: {acesso?.lan_endpoint ?? 'não configurado (rede.servidor.endpoint_lan) — aparelho de outra máquina é recusado'}</span>
              {fw ? <Badge tone={FIREWALL[fw.state].tom} size="sm" title={fw.detail}>{FIREWALL[fw.state].rotulo}</Badge>
                  : <Badge tone="neutral" size="sm">firewall ainda não lido</Badge>}
            </p>
            {fw ? <p className={s.legend}>{fw.detail} (lido em {fw.checked_at})</p> : null}
            {fw && fw.warnings && fw.warnings.length > 0 ? <p className={s.dupe}>{fw.warnings.join(' ')}</p> : null}
            {fw && fw.commands.length > 0 ? (
              <div>
                <p className={s.legend}>O dono roda, num PowerShell de administrador do central (a plataforma não mexe no firewall), e depois pede Reaplicar:</p>
                <pre aria-label="Comando do firewall" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontSize: 'var(--fs-xs)' }}>
                  {fw.commands.join('\n')}
                </pre>
                {fw.inspect_command ? (
                  <details>
                    <summary className={s.legend}>Conferir a regra e desfazer</summary>
                    <pre aria-label="Inspeção e reversão do firewall" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontSize: 'var(--fs-xs)' }}>
                      {[fw.inspect_command, fw.revert_command].filter(Boolean).join('\n')}
                    </pre>
                  </details>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
      </CardBody>
    </Card>
  );
}
