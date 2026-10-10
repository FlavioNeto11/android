/**
 * Editar a saída esperada de um perfil de rede (31.301, adendo v1.134): `PUT /api/network/profiles/{id}`.
 *
 * Antes não havia como: no android-05 (10/10) o `egress_esperado` do perfil do igfarm foi regravado por SQL direto, o IP da
 * criação virou o IP medido e a comparação saída medida × esperada deixou de provar algo. Agora a troca passa pelo app, com
 * antes e depois, e o perfil de uma conta do igfarm em uso só muda com um motivo (409 `egress_esperado_protegido`).
 * Trocar o esperado NÃO é remédio para proxy que rotaciona: o remédio é sessão fixa no proxy.
 */
import { Pencil, ShieldAlert } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api, toApiError } from '../../api/client';
import type { NetworkProfileListed } from '../../api/types';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Field, TextInput } from '../../components/Field';
import { formatClockComFuso } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import styles from './Rede.module.css';

const MOTIVO_MAX = 300;

type Familia = 'egress_esperado' | 'egress_esperado_ipv6';
type Saida = Record<Familia, string | null>;

const FAMILIAS: { chave: Familia; rotulo: string; exemplo: string }[] = [
  { chave: 'egress_esperado', rotulo: 'Saída esperada IPv4', exemplo: '203.0.113.10' },
  { chave: 'egress_esperado_ipv6', rotulo: 'Saída esperada IPv6', exemplo: '2001:db8::10' },
];

/** O que o perfil declara hoje, por família (`null` = não declara). */
export function saidaAtual(p: Pick<NetworkProfileListed, 'params'>): Saida {
  const ler = (k: Familia): string | null => {
    const v = p.params[k];
    return typeof v === 'string' && v.length > 0 ? v : null;
  };
  return { egress_esperado: ler('egress_esperado'), egress_esperado_ipv6: ler('egress_esperado_ipv6') };
}

/**
 * O corpo do PUT só com o que mudou: campo omitido fica como está, `null` tira a saída daquela família. Vazio onde já
 * havia valor tira; vazio onde não havia nada não manda nada.
 */
export function corpoDaTroca(atual: Saida, digitado: Record<Familia, string>): Partial<Saida> {
  const corpo: Partial<Saida> = {};
  for (const { chave } of FAMILIAS) {
    const novo = digitado[chave].trim() || null;
    if (novo !== atual[chave]) corpo[chave] = novo;
  }
  return corpo;
}

const mostrar = (v: string | null | undefined): string => v || 'sem saída esperada';

interface Protecao {
  mensagem: string;
  contaIgfarm: string | null;
  emUso: string[];
}

/** Lê o corpo do 409 `egress_esperado_protegido`: a mensagem do servidor e quem pede o perfil. */
function lerProtecao(detail: Record<string, unknown> | null, mensagem: string): Protecao {
  const emUso = Array.isArray(detail?.in_use) ? (detail.in_use as unknown[]).filter((x): x is string => typeof x === 'string') : [];
  const conta = typeof detail?.account_id === 'string' ? detail.account_id : null;
  return { mensagem, contaIgfarm: conta, emUso };
}

/** As trocas desta saída que o painel viu chegar nesta sessão (`network.updated` · `perfil_atualizado`), da mais nova à mais antiga. */
function useTrocasDaSessao(profileId: string) {
  const eventos = useAppStore((s) => s.recentEvents);
  // O store guarda os eventos na ordem de chegada (a mais antiga primeiro): inverte, para a lista começar pela troca mais nova.
  return eventos.filter((e) => e.kind === 'network.updated' && e.data?.acao === 'perfil_atualizado' && e.data?.profile_id === profileId).reverse();
}

function Antes({ d }: { d: unknown }) {
  const o = (d && typeof d === 'object' ? d : {}) as Record<string, unknown>;
  const t = (k: Familia) => (typeof o[k] === 'string' && o[k] ? (o[k] as string) : null);
  return <>{mostrar(t('egress_esperado'))}{t('egress_esperado_ipv6') ? ` · ${t('egress_esperado_ipv6')}` : ''}</>;
}

export function EditarSaidaEsperada({ perfil, onSalvo, pedido = null }: {
  perfil: NetworkProfileListed; onSalvo: () => Promise<void>;
  /** Muda a cada pedido de abertura vindo de fora (o atalho da saída divergente, 31.303); `null` = ninguém pediu. */
  pedido?: number | null;
}) {
  const [aberto, setAberto] = useState(false);
  const atual = saidaAtual(perfil);
  const [digitado, setDigitado] = useState<Record<Familia, string>>({
    egress_esperado: atual.egress_esperado ?? '', egress_esperado_ipv6: atual.egress_esperado_ipv6 ?? '',
  });
  const [motivo, setMotivo] = useState('');
  const [protecao, setProtecao] = useState<Protecao | null>(null);
  const [recusa, setRecusa] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const trocas = useTrocasDaSessao(perfil.id);

  const corpo = corpoDaTroca(atual, digitado);
  const mudou = Object.keys(corpo).length > 0;

  function abrir() {
    setDigitado({ egress_esperado: atual.egress_esperado ?? '', egress_esperado_ipv6: atual.egress_esperado_ipv6 ?? '' });
    setMotivo('');
    setProtecao(null);
    setRecusa(null);
    setAberto(true);
  }

  const raiz = useRef<HTMLDivElement>(null);
  const ultimoPedido = useRef<number | null>(null);
  useEffect(() => {
    if (pedido === null || pedido === ultimoPedido.current) return;
    ultimoPedido.current = pedido;
    abrir();
    raiz.current?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
    // `abrir` só lê o perfil de agora; o token é o que dispara.
  }, [pedido]);

  async function salvar() {
    if (!mudou || ocupado) return;
    setOcupado(true);
    setRecusa(null);
    let salvou = false;
    try {
      await api.updateNetworkProfileSaida(perfil.id, { ...corpo, ...(motivo.trim() ? { motivo: motivo.trim() } : {}) });
      toast({ tone: 'success', title: `Saída esperada de ${perfil.name} atualizada`,
              message: 'Nada foi reavaliado: a próxima medição do aparelho compara com o valor novo.' });
      salvou = true;
      setAberto(false);
    } catch (e) {
      const err = toApiError(e);
      if (err.status === 409 && err.code === 'egress_esperado_protegido') {
        setProtecao(lerProtecao(err.detail, err.message));
      } else if (err.status === 422 && err.code === 'invalid_egress') {
        setRecusa(err.message);
      } else {
        toastError('Não foi possível trocar a saída esperada', e);
      }
    } finally {
      setOcupado(false);
    }
    if (salvou) await onSalvo();
  }

  const motivoObrigatorio = protecao !== null;
  const bloqueio = !mudou ? 'Nada mudou.' : motivoObrigatorio && !motivo.trim() ? 'Diga o motivo da troca.' : null;

  return (
    <div className={styles.saidaEditor} ref={raiz}>
      <div className={styles.saidaTopo}>
        <Button size="sm" variant="ghost" icon={Pencil} onClick={() => (aberto ? setAberto(false) : abrir())}
                aria-expanded={aberto} label={`Editar a saída esperada de ${perfil.name}`}>
          {aberto ? 'Fechar' : 'Editar saída esperada'}
        </Button>
      </div>
      {trocas.length > 0 ? (
        <ul className={styles.saidaTrocas} aria-label={`Trocas da saída esperada de ${perfil.name} nesta sessão`}>
          {trocas.slice(0, 3).map((e) => (
            <li key={e.id ?? e.ts}>
              {formatClockComFuso(e.ts)} · <Antes d={e.data?.antes} /> → <Antes d={e.data?.depois} />
              {typeof e.data?.motivo === 'string' && e.data.motivo ? ` · motivo: ${e.data.motivo}` : ''}
              {typeof e.data?.quem === 'string' && e.data.quem ? ` · por ${e.data.quem}` : ''}
            </li>
          ))}
        </ul>
      ) : null}
      {aberto ? (
        <div className={styles.saidaForm}>
          {FAMILIAS.map((f) => (
            <Field key={f.chave} label={f.rotulo} unit="vazio tira"
                   error={recusa && corpo[f.chave] !== undefined && corpo[f.chave] !== null ? recusa : undefined}>
              {({ id, describedBy, invalid }) => (
                <TextInput id={id} aria-describedby={describedBy} invalid={invalid} mono maxLength={45} placeholder={f.exemplo}
                           value={digitado[f.chave]} disabled={ocupado}
                           onChange={(e) => { setDigitado((d) => ({ ...d, [f.chave]: e.target.value })); setRecusa(null); }} />
              )}
            </Field>
          ))}
          <p className={styles.saidaAntesDepois} aria-live="polite">
            {mudou ? (
              <>
                {FAMILIAS.filter((f) => corpo[f.chave] !== undefined).map((f) => (
                  <span key={f.chave}>{f.rotulo.replace('Saída esperada ', '')}: {mostrar(atual[f.chave])} → {mostrar(corpo[f.chave])}. </span>
                ))}
              </>
            ) : 'Sem mudança ainda.'}
          </p>
          {protecao ? (
            <Banner tone="warning" icon={ShieldAlert} title="Este IP esperado é a prova do aparelho em uso" role="alert">
              <p>{protecao.mensagem}</p>
              {protecao.contaIgfarm || protecao.emUso.length > 0 ? (
                <p>
                  {protecao.contaIgfarm ? `Conta do igfarm: ${protecao.contaIgfarm}. ` : ''}
                  {protecao.emUso.length > 0 ? `Em uso por: ${protecao.emUso.join(', ')}.` : ''}
                </p>
              ) : null}
            </Banner>
          ) : null}
          {protecao ? (
            <Field label="Motivo da troca" unit={`${motivo.length}/${MOTIVO_MAX}`}
                   hint="Texto curto, sem senha nem segredo: ele vai ao evento. Trocar o esperado não resolve proxy que rotaciona; o remédio é sessão fixa no proxy.">
              {({ id, describedBy }) => (
                <TextInput id={id} aria-describedby={describedBy} maxLength={MOTIVO_MAX} value={motivo} disabled={ocupado}
                           onChange={(e) => setMotivo(e.target.value)} />
              )}
            </Field>
          ) : null}
          <div className={styles.saidaAcoes}>
            <Button loading={ocupado} disabledReason={bloqueio} onClick={() => void salvar()}>
              {protecao ? 'Trocar mesmo assim' : 'Salvar saída esperada'}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
