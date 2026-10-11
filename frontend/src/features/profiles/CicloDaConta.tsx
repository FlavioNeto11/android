import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { CicloDaConta as Ciclo, ContatoDaConta } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Disclosure } from '../../components/Disclosure';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { formatInt } from '../../lib/format';
import { type LoadError, LoadErrorState, toLoadError } from '../../lib/loadError';
import { formatDateTime } from '../../lib/time';
import styles from './Profiles.module.css';

/**
 * 31.346 (adendos v1.145 e v1.149): o ciclo de uma conta do Instagram, só leitura. Da criação (ou do planejamento, quando a conta
 * do app ainda não foi confirmada) ao primeiro contato com o app, cada tentativa com o desfecho e, se houver, a retirada.
 * A API não devolve @, e-mail, senha nem IP: o painel só mostra ids, horas, minutos e desfechos. Desfecho que o painel
 * não conhece aparece com o código que o servidor mandou (o motor pode ganhar desfechos sem o painel mudar).
 */

const DESFECHOS: Record<string, { texto: string; tom: 'success' | 'warning' | 'danger' | 'neutral' }> = {
  session_ready: { texto: 'Entrou (sessão pronta)', tom: 'success' },
  confirmada: { texto: 'Cadastro confirmado', tom: 'success' },
  auth_challenge: { texto: 'Pediu um desafio de segurança', tom: 'warning' },
  uncertain: { texto: 'Resultado incerto', tom: 'warning' },
  parada: { texto: 'Cadastro parado', tom: 'warning' },
  conta_nao_encontrada: { texto: 'Conta não encontrada no app', tom: 'danger' },
};

export function desfechoEmPalavras(codigo: string | null | undefined): { texto: string; tom: 'success' | 'warning' | 'danger' | 'neutral' } {
  if (!codigo) return { texto: 'sem desfecho', tom: 'neutral' };
  return DESFECHOS[codigo] ?? { texto: codigo, tom: 'neutral' };
}

/** Minutos legíveis: "menos de 1 min", "42 min", "3 h 05 min", "2 d 4 h". */
export function minutosEmPalavras(min: number | null | undefined): string {
  if (min === null || min === undefined || !Number.isFinite(min)) return 'não medido';
  if (min < 1) return 'menos de 1 min';
  const total = Math.round(min);
  if (total < 60) return `${formatInt(total)} min`;
  if (total < 1440) return `${Math.floor(total / 60)} h ${String(total % 60).padStart(2, '0')} min`;
  return `${Math.floor(total / 1440)} d ${Math.floor((total % 1440) / 60)} h`;
}

export function origemEmPalavras(c: Pick<Ciclo, 'origem'>): string {
  return c.origem === 'app' ? 'Criada no app (cadastro guiado)' : 'Criada pelo igfarm';
}

/** De onde contam os minutos: a criação, ou o planejamento enquanto a conta do app não foi confirmada. */
export function referenciaEmPalavras(c: Pick<Ciclo, 'referencia'>): string {
  return c.referencia === 'planejamento'
    ? 'minutos desde o planejamento (a conta ainda não foi confirmada)'
    : 'minutos desde a criação';
}

function Contato({ c, desde }: { c: ContatoDaConta; desde: string }) {
  const d = desfechoEmPalavras(c.desfecho);
  return (
    <li className={styles.filaItem}>
      <div className={styles.filaInfo}>
        <p className={`${styles.filaPerfil} ${styles.filaPerfilQuebra}`}>
          <Badge tone={d.tom} size="sm">{d.texto}</Badge>
          {c.etapa === 'cadastro' ? <span className={styles.muted}>cadastro</span> : null}
          <span className={styles.muted}>{formatDateTime(c.iniciado_em)}</span>
        </p>
        <p className={styles.filaDetalhe}>
          <span className={styles.muted}>{minutosEmPalavras(c.minutos_desde_a_criacao)} {desde}</span>
        </p>
        {c.detalhe ? <p className={styles.filaMotivo}>{c.detalhe}</p> : null}
      </div>
    </li>
  );
}

/** O corpo (já carregado) do ciclo. Separado da leitura para o teste e para a tela de ficha. */
export function CorpoDoCiclo({ ciclo }: { ciclo: Ciclo }) {
  const referencia = referenciaEmPalavras(ciclo);
  const inicio = ciclo.referencia === 'planejamento' ? ciclo.registrada_em : ciclo.criada_em;
  return (
    <div className={styles.configStack}>
      <p className={styles.detail}>
        <strong>{origemEmPalavras(ciclo)}</strong>
        {inicio ? <> · {ciclo.referencia === 'planejamento' ? 'planejada' : 'criada'} em {formatDateTime(inicio)}</> : null}
      </p>
      <p className={styles.detail}>
        Primeiro contato com o app: {ciclo.minutos_ate_o_primeiro_contato === null ? 'ainda não houve' : minutosEmPalavras(ciclo.minutos_ate_o_primeiro_contato)}
        {' '}<span className={styles.muted}>({referencia})</span>
      </p>
      {ciclo.estado === 'retirada' ? (
        <p className={styles.detail}>
          <Badge tone="danger" size="sm">retirada</Badge> {ciclo.retirada_em ? `em ${formatDateTime(ciclo.retirada_em)}` : 'sem hora registrada'}
          {' · '}a conta saiu da plataforma; o motivo do bloqueio, quando registrado, está na ficha da persona.
        </p>
      ) : <p className={styles.detail}><Badge tone="success" size="sm">ativa</Badge> a conta segue na persona.</p>}
      {ciclo.contatos.length === 0 ? (
        <p className={styles.detail}>Nenhum contato com o app registrado ainda.</p>
      ) : (
        <ul className={styles.filaLista} aria-label="Contatos da conta com o app">
          {ciclo.contatos.map((c, i) => <Contato key={`${c.iniciado_em ?? i}|${c.etapa ?? ''}|${i}`} c={c} desde={ciclo.referencia === 'planejamento' ? 'desde o planejamento' : 'desde a criação'} />)}
        </ul>
      )}
    </div>
  );
}

function LeituraDoCiclo({ accountId }: { accountId: string }) {
  const [ciclo, setCiclo] = useState<Ciclo | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [tentativa, setTentativa] = useState(0);

  const ler = useCallback(() => setTentativa((n) => n + 1), []);
  useEffect(() => {
    let vivo = true;
    setErro(null);
    api.getCicloDaConta(accountId)
      .then((c) => { if (vivo) setCiclo(c); })
      .catch((e) => { if (vivo) setErro(toLoadError(e)); });
    return () => { vivo = false; };
  }, [accountId, tentativa]);

  if (erro && !ciclo) return <LoadErrorState what="o ciclo da conta" error={erro} onRetry={ler} compact />;
  if (!ciclo) {
    return <LoadingRegion label="Carregando o ciclo da conta…" className={styles.configStack}><Skeleton height={48} radius={8} /></LoadingRegion>;
  }
  return <CorpoDoCiclo ciclo={ciclo} />;
}

/** A seção "Ciclo da conta" de uma conta do Instagram: recolhida, e só lê a API quando a pessoa a abre. */
export function CicloDaConta({ accountId }: { accountId: string }) {
  return (
    <Disclosure bare summary="Ciclo da conta">
      {() => <LeituraDoCiclo accountId={accountId} />}
    </Disclosure>
  );
}
