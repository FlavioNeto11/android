/**
 * Item 11.8 — "Configurações do perfil sem formulário gigante" (pedido do dono em 24/09).
 *
 * Antes a aba Configurações era uma lista achatada de ~20 ações, cada uma com um `<select>` escondendo as três
 * opções de política — um formulário gigante que não dizia nada sobre risco. Aqui: as ações agrupadas por natureza (as mesmas fronteiras dos
 * comentários de `catalog/instagram.py`), cada política como controle segmentado (as opções todas visíveis, sem
 * abrir menu). Os limites por hora e por dia saíram com o ADR-083: o backend não aplica mais nenhum (31.276).
 */
import { Badge } from '../../components/Badge';
import type { Capability, PolicyName } from '../../api/types';
import { cx } from '../../lib/format';
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
