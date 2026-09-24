/**
 * Item 11.8 — "Configurações do perfil sem formulário gigante" (pedido do dono em 24/09).
 *
 * Antes a aba Configurações era uma lista achatada de ~20 ações, cada uma com um `<select>` escondendo as três
 * opções de política, mais um bloco de números soltos para os limites — um formulário gigante que não dizia
 * nada sobre risco nem sobre uso real. Aqui: as ações agrupadas por natureza (as mesmas fronteiras dos
 * comentários de `catalog/instagram.py`), cada política como controle segmentado (as opções todas visíveis, sem
 * abrir menu) e os limites como medidor de uso contra o teto, não só um número.
 */
import type { ReactNode } from 'react';
import { Badge } from '../../components/Badge';
import type { Capability, PolicyName, SocialInteraction } from '../../api/types';
import { clamp01, cx } from '../../lib/format';
import type { Tone } from '../../lib/status';
import styles from './Profiles.module.css';

// ---------------------------------------------------------------- agrupamento por natureza

export type PolicyGroupKey = 'sessao' | 'navegacao' | 'leitura' | 'efeito';

export const POLICY_GROUP_LABEL: Record<PolicyGroupKey, string> = {
  sessao: 'Sessão',
  navegacao: 'Navegação',
  leitura: 'Leitura',
  efeito: 'Efeito externo',
};

const POLICY_GROUP_ORDER: PolicyGroupKey[] = ['sessao', 'navegacao', 'leitura', 'efeito'];

/**
 * A que grupo uma ação pertence. As chaves do catálogo seguem uma convenção de prefixo estável (medida em
 * `catalog/instagram.py`: `OPEN_*` é navegação sem efeito, `READ_*`/`COLLECT_*` é leitura, o resto com efeito
 * colateral é "efeito externo"); o que sobra sem prefixo reconhecido e sem efeito é sessão. Uma chave nova que
 * não bata com nenhum prefixo cai em "efeito externo" quando tem `side_effect`, e em "sessão" quando não tem —
 * nunca desaparece da tela.
 */
export function grupoDaAcao(c: Capability): PolicyGroupKey {
  if (/^(AUTHENTICATE|VERIFY_ACCOUNT|LOGIN|LOGOUT)/.test(c.key)) return 'sessao';
  if (c.key.startsWith('OPEN_')) return 'navegacao';
  if (c.key.startsWith('READ_') || c.key.startsWith('COLLECT_')) return 'leitura';
  return c.side_effect ? 'efeito' : 'sessao';
}

export interface PolicyGroup {
  chave: PolicyGroupKey;
  label: string;
  itens: Capability[];
}

/** Agrupa e só devolve os grupos que têm ao menos uma ação — um app menor que o Instagram não mostra "Leitura" vazia. */
export function agruparAcoes(acoes: Capability[]): PolicyGroup[] {
  const porGrupo = new Map<PolicyGroupKey, Capability[]>();
  for (const c of acoes) {
    const g = grupoDaAcao(c);
    const lista = porGrupo.get(g);
    if (lista) lista.push(c);
    else porGrupo.set(g, [c]);
  }
  return POLICY_GROUP_ORDER
    .filter((g) => (porGrupo.get(g) ?? []).length > 0)
    .map((g) => ({ chave: g, label: POLICY_GROUP_LABEL[g], itens: porGrupo.get(g) as Capability[] }));
}

// ---------------------------------------------------------------- selo de risco

export const RISK_LABEL: Record<string, string> = { low: 'baixo', medium: 'médio', high: 'alto' };
export const RISK_TONE: Record<string, Tone> = { low: 'neutral', medium: 'warning', high: 'danger' };

export function RiskBadge({ risk }: { risk: string }) {
  return <Badge tone={RISK_TONE[risk] ?? 'neutral'} size="sm">risco {RISK_LABEL[risk] ?? risk}</Badge>;
}

// ---------------------------------------------------------------- controle segmentado da política

export const POLICY_SEGMENT_OPTIONS: { value: PolicyName; label: string }[] = [
  { value: 'autonomous', label: 'Sozinho' },
  { value: 'approval_required', label: 'Com aprovação' },
  { value: 'manual_only', label: 'Só manual' },
];

/**
 * Três botões lado a lado em vez de um `<select>`: as opções ficam todas visíveis, a ativa marcada. `disabled`
 * é raro no catálogo de hoje (nenhuma ação nasce assim) mas pode vir de fora; quando o valor atual não é
 * nenhuma das três opções, nenhum botão fica marcado e o selo de "desligado" ao lado do controle avisa —
 * ver `AbaConfiguracoes`.
 */
export function PolicySegmented({ value, disabled, onChange, ariaLabel }: {
  value: PolicyName;
  disabled?: boolean;
  onChange: (valor: PolicyName) => void;
  ariaLabel: string;
}) {
  return (
    <div className={styles.segmented} role="radiogroup" aria-label={ariaLabel}>
      {POLICY_SEGMENT_OPTIONS.map((opt) => {
        const ativo = value === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={ativo}
            className={cx(styles.segmentedBtn, ativo && styles.segmentedBtnActive)}
            disabled={disabled}
            onClick={() => onChange(opt.value)}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- limites como medidor

/** A que grupo de limite (o "balde" do `limit_bucket`) cada tipo de interação confirmada pertence — mesma
 *  divisão que `Capability.limit_bucket` usa no catálogo (curtir/descurtir → likes, comentar/responder →
 *  comments, mensagem → dms, seguir/deixar de seguir/aceitar/recusar pedido → follows). */
const TIPO_PARA_BALDE: Record<string, string> = {
  post_liked: 'likes', post_unliked: 'likes', comment_liked: 'likes',
  comment_replied: 'comments',
  dm_sent: 'dms',
  followed: 'follows', unfollowed: 'follows', follow_request_accepted: 'follows', follow_request_declined: 'follows',
};

/** Quanto do limite já foi usado HOJE (dia local), contado só das interações já confirmadas — o mesmo padrão de
 *  "prova, não promessa" do resto do painel: pendente ou falhada não consome o teto. */
export function contarUsoDeHoje(interacoes: SocialInteraction[], agora: Date = new Date()): Record<string, number> {
  const hoje = agora.toDateString();
  const contagem: Record<string, number> = {};
  for (const it of interacoes) {
    if (it.status !== 'confirmed') continue;
    const quandoIso = it.occurred_at || it.created_at;
    const quando = quandoIso ? new Date(quandoIso) : null;
    if (!quando || Number.isNaN(quando.getTime()) || quando.toDateString() !== hoje) continue;
    const balde = TIPO_PARA_BALDE[it.type];
    if (!balde) continue;
    contagem[balde] = (contagem[balde] ?? 0) + 1;
  }
  return contagem;
}

/** O nome do limite (`likes_per_hour`) → o balde que a contagem de uso usa (`likes`). Só limites "por hora" têm
 *  contrapartida em interações contáveis; `actions_per_run` e o intervalo entre ações não têm o que medir aqui. */
export function baldeDoLimite(chaveDoLimite: string): string | null {
  const m = /^(.+)_per_hour$/.exec(chaveDoLimite);
  return m ? m[1] ?? null : null;
}

/** Cartão com medidor: uso de hoje × limite, cor por proximidade do teto. A edição do número fica no `children`
 *  (o chamador decide o campo — `AbaConfiguracoes` usa o mesmo `TextInput`/`onBlur` de antes). */
export function LimitMeterCard({ label, usado, limite, children }: {
  label: string;
  /** null = sem contagem para este limite (ex.: `actions_per_run`); `undefined` = não há uso a mostrar (grupo). */
  usado: number | null | undefined;
  limite: number;
  children?: ReactNode;
}) {
  const proporcao = usado != null && limite > 0 ? clamp01(usado / limite) : 0;
  const tone: Tone = usado == null ? 'neutral' : proporcao >= 1 ? 'danger' : proporcao >= 0.7 ? 'warning' : 'success';
  const pct = Math.round(proporcao * 100);
  return (
    <div className={styles.limitCard}>
      <div className={styles.limitCardHead}>
        <span className={styles.limitCardLabel}>{label}</span>
        {usado === undefined ? null : usado !== null ? (
          <Badge tone={tone} size="sm">{usado}/{limite} hoje</Badge>
        ) : (
          <Badge tone="neutral" size="sm">sem contagem de uso</Badge>
        )}
      </div>
      {usado != null ? (
        <div
          className={cx(styles.limitMeter, `tone-${tone}`)}
          role="progressbar"
          aria-label={`Uso de hoje de ${label}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
        >
          <div className={styles.limitMeterFill} data-tone={tone} style={{ width: `${pct}%` }} />
        </div>
      ) : null}
      {children}
    </div>
  );
}
