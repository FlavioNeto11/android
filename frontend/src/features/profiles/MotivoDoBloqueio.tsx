import { Ban } from 'lucide-react';
import { useMemo } from 'react';
import type { ContaRetirada, MotivoDoBloqueio as Motivo } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { isRecord } from '../../lib/format';
import { tempoRelativo, useNow } from '../../lib/time';
import { useAppStore } from '../../store/app';
import styles from './Profiles.module.css';

const texto = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);
const numero = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** Lê o objeto do evento sem confiar nele: cada campo que não tem o tipo certo vira `null` (nunca se inventa). */
export function lerMotivo(bruto: unknown): Motivo | null {
  if (!isRecord(bruto)) return null;
  return {
    egresso_esperado: texto(bruto.egresso_esperado),
    egresso_medido: texto(bruto.egresso_medido),
    egresso_divergente: typeof bruto.egresso_divergente === 'boolean' ? bruto.egresso_divergente : null,
    ips_distintos_desde_criacao: numero(bruto.ips_distintos_desde_criacao),
    minutos_ate_o_primeiro_login: numero(bruto.minutos_ate_o_primeiro_login),
    trecho_da_tela: texto(bruto.trecho_da_tela),
  };
}

/** As linhas do padrão que levou ao bloqueio, só com o que se sabe; vazio = "sem motivo registrado". */
export function linhasDoMotivo(m: Motivo | null): string[] {
  if (!m) return [];
  const linhas: string[] = [];
  if (m.egresso_esperado || m.egresso_medido) {
    linhas.push(`Saída (IP): esperada ${m.egresso_esperado ?? 'não registrada'}, medida ${m.egresso_medido ?? 'não medida'}`
      + (m.egresso_divergente === true ? ' — divergente' : m.egresso_divergente === false ? ' — igual' : ''));
  }
  if (m.ips_distintos_desde_criacao !== null) {
    linhas.push(m.ips_distintos_desde_criacao === 1
      ? '1 IP de saída desde a criação (sem rotação)'
      : `${m.ips_distintos_desde_criacao} IPs de saída distintos desde a criação`);
  }
  if (m.minutos_ate_o_primeiro_login !== null) {
    linhas.push(`${String(m.minutos_ate_o_primeiro_login).replace('.', ',')} min entre a criação da conta e o primeiro login`);
  }
  if (m.trecho_da_tela) linhas.push(`Tela: “${m.trecho_da_tela}”`);
  return linhas;
}

export interface Bloqueio { quando: string; app: string | null; motivo: Motivo | null }

/** Bloqueios desta persona que o painel viu chegar (evento `profile.account_retired`), do mais novo ao mais antigo. */
export function bloqueiosDaPersona(
  eventos: readonly { kind: string; ts: string; data: Record<string, unknown> | null }[], profileId: string,
): Bloqueio[] {
  return eventos
    .filter((e) => e.kind === 'profile.account_retired' && e.data?.profile_id === profileId)
    .map((e) => ({ quando: e.ts, app: texto(e.data?.app_id), motivo: lerMotivo(e.data?.motivo_do_bloqueio) }))
    .reverse();
}

const JANELA_MS = 120_000;

/**
 * Une o que o servidor guardou (`contas_retiradas` da persona, v1.142) com o evento ao vivo que ainda não está na leitura:
 * o bloqueio que chegou agora aparece na hora e não duplica quando a ficha recarrega e a lápide vem junto. Do mais novo
 * ao mais antigo.
 */
export function unirBloqueios(guardados: readonly ContaRetirada[] | undefined, aoVivo: readonly Bloqueio[]): Bloqueio[] {
  const lapides: Bloqueio[] = (guardados ?? []).map((c) => ({ quando: c.retirada_em, app: c.app_id, motivo: lerMotivo(c.motivo_do_bloqueio) }));
  const novos = aoVivo.filter((v) => !lapides.some((l) => (!v.app || !l.app || l.app === v.app)
    && Math.abs(Date.parse(l.quando) - Date.parse(v.quando)) <= JANELA_MS));
  return [...lapides, ...novos].sort((a, b) => (Date.parse(b.quando) || 0) - (Date.parse(a.quando) || 0));
}

/**
 * Motivo do bloqueio na ficha da persona (31.326): por que a conta caiu na tela humana. A fonte estável é
 * `contas_retiradas` do GET da persona (a lápide, v1.142); o evento ao vivo `profile.account_retired` cobre o intervalo
 * até a ficha ser relida. Sem nenhum dos dois, o cartão nem aparece.
 */
export function MotivoDoBloqueio({ profileId, retiradas }: { profileId: string; retiradas?: readonly ContaRetirada[] }) {
  const eventos = useAppStore((s) => s.recentEvents);
  const now = useNow();
  const bloqueios = useMemo(
    () => unirBloqueios(retiradas, bloqueiosDaPersona(eventos, profileId)), [eventos, profileId, retiradas]);
  if (bloqueios.length === 0) return null;
  return (
    <Card>
      <CardHeader title={<span className={styles.filaTitulo}><Ban size={18} aria-hidden /> Conta bloqueada</span>}
                  subtitle="A plataforma pediu para provar que é uma pessoa (tela humana). O bloqueio é definitivo: a conta saiu da plataforma. Estes são os dados que o servidor guardou do que levou a isso." />
      <CardBody>
        <ul className={styles.filaLista}>
          {bloqueios.map((b) => {
            const linhas = linhasDoMotivo(b.motivo);
            return (
              <li key={`${b.quando}|${b.app ?? ''}`} className={styles.filaItem}>
                <div className={styles.filaInfo}>
                  <p className={styles.filaPerfil}>
                    <Badge tone="danger" size="sm">bloqueada</Badge>
                    {b.app ? <span className={styles.muted}>{b.app}</span> : null}
                    <span className={styles.muted}>· {tempoRelativo(b.quando, now)}</span>
                  </p>
                  {linhas.length
                    ? linhas.map((l) => <p key={l} className={styles.filaMotivo}>{l}</p>)
                    : <p className={styles.filaMotivo}>Sem motivo registrado para este bloqueio.</p>}
                </div>
              </li>
            );
          })}
        </ul>
      </CardBody>
    </Card>
  );
}

/**
 * A lista de contas bloqueadas da tela Personas (31.326): uma linha por conta retirada, de todas as personas, com o motivo.
 * Só aparece quando alguma persona tem `contas_retiradas` (v1.142); some sem elas.
 */
export function ListaDeContasRetiradas({ personas, abrir }: {
  personas: readonly { id: string; name?: string | null; display_name?: string | null; contas_retiradas?: ContaRetirada[] }[];
  abrir?: (profileId: string) => void;
}) {
  const now = useNow();
  const linhas = useMemo(() => personas.flatMap((p) => (p.contas_retiradas ?? []).map((c) => ({
    id: p.id, nome: p.display_name || p.name || p.id, conta: c, motivo: linhasDoMotivo(lerMotivo(c.motivo_do_bloqueio)),
  }))).sort((a, b) => (Date.parse(b.conta.retirada_em) || 0) - (Date.parse(a.conta.retirada_em) || 0)), [personas]);
  if (linhas.length === 0) return null;
  return (
    <Card>
      <CardHeader title={<span className={styles.filaTitulo}><Ban size={18} aria-hidden /> Contas retiradas por bloqueio <Badge tone="danger">{linhas.length}</Badge></span>}
                  subtitle="Contas que a plataforma bloqueou na tela humana e saíram do parque. A persona fica; o motivo é o que o servidor guardou." />
      <CardBody>
        <ul className={styles.filaLista} aria-label="Contas retiradas por bloqueio">
          {linhas.map((l) => (
            <li key={`${l.id}|${l.conta.app_id}|${l.conta.retirada_em}`} className={styles.filaItem}>
              <div className={styles.filaInfo}>
                <p className={styles.filaPerfil}>
                  {abrir ? <button type="button" className={styles.estadoAcionavel} onClick={() => abrir(l.id)}>{l.nome}</button>
                    : <span className={styles.filaUsuario}>{l.nome}</span>}
                  <Badge tone="danger" size="sm">bloqueada</Badge>
                  <span className={styles.muted}>{l.conta.app_id} · {tempoRelativo(l.conta.retirada_em, now)}</span>
                </p>
                {l.motivo.length
                  ? l.motivo.map((t) => <p key={t} className={styles.filaMotivo}>{t}</p>)
                  : <p className={styles.filaMotivo}>Sem motivo registrado para este bloqueio.</p>}
              </div>
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}
