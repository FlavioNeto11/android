/**
 * Editor de acesso compartilhado entre o PERFIL (aba Configurações) e o GRUPO DE ACESSO (tela de Perfis).
 *
 * O mesmo desenho nos dois lugares — ações agrupadas por natureza, política em controle segmentado, limites como
 * medidor — muda só de onde vem cada valor. No perfil há três camadas: a escolha própria (sobrepõe), o grupo
 * (herdado) e o padrão do catálogo. No grupo há duas: o que o grupo define e o padrão. Em qualquer nível,
 * "herdar" APAGA a escolha daquele nível (manda `null`) em vez de gravar uma cópia do valor de baixo — gravar a
 * cópia prenderia o perfil contra o grupo para sempre, que é exatamente o que o grupo existe para evitar.
 */
import { ChevronRight, Undo2 } from 'lucide-react';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Field, TextInput } from '../../components/Field';
import type { Capability, PolicyName } from '../../api/types';
import type { Tone } from '../../lib/status';
import { agruparAcoes, LimitMeterCard, POLICY_SEGMENT_OPTIONS, PolicySegmented, RiskBadge } from './PolicyVisual';
import styles from './Profiles.module.css';

export const POLICY_LABEL: Record<PolicyName, string> = {
  autonomous: 'Sozinho',
  approval_required: 'Com aprovação',
  manual_only: 'Só manual',
  disabled: 'Desligado',
};

export const LIMIT_LABEL: Record<string, string> = {
  likes_per_hour: 'Curtidas por hora',
  comments_per_hour: 'Comentários por hora',
  follows_per_hour: 'Seguir/deixar de seguir por hora',
  dms_per_hour: 'Mensagens por hora',
  likes_per_day: 'Curtidas por dia',
  comments_per_day: 'Comentários por dia',
  follows_per_day: 'Seguir/deixar de seguir por dia',
  dms_per_day: 'Mensagens por dia',
  actions_per_run: 'Ações com efeito por execução',
  cooldown_between_external_actions_s: 'Intervalo entre ações (segundos)',
  warmup_days: 'Aquecimento de conta nova (dias)',
  warmup_percent: 'No aquecimento, % dos tetos',
};

/** Como a origem de um valor aparece: quem decidiu o que vale agora. */
export interface Origem {
  /** Este nível tem escolha própria (é o que o botão "herdar" apaga). */
  propria: boolean;
  rotulo: string;
  tone: Tone;
}

export function resumoDePoliticas(acoes: Capability[], efetivo: (c: Capability) => PolicyName): string {
  const n: Record<PolicyName, number> = { autonomous: 0, approval_required: 0, manual_only: 0, disabled: 0 };
  for (const c of acoes) n[efetivo(c)] += 1;
  return `${n.autonomous} sozinho · ${n.approval_required} com aprovação · ${n.manual_only} só manual`
    + (n.disabled ? ` · ${n.disabled} desligado` : '');
}

export function PolicyActionsEditor({ acoes, efetivo, origem, herdaria, nomeDaHeranca, loosened, salvando, onChange }: {
  acoes: Capability[];
  efetivo: (c: Capability) => PolicyName;
  origem: (c: Capability) => Origem;
  /** O que passaria a valer se a escolha deste nível fosse apagada. */
  herdaria: (c: Capability) => PolicyName;
  /** "do grupo Cautelosos", "do padrão do catálogo" — completa o botão "herdar". */
  nomeDaHeranca: (c: Capability) => string;
  loosened: string[];
  salvando: boolean;
  onChange: (keys: string[], valor: PolicyName | null) => void;
}) {
  const categorias = agruparAcoes(acoes);
  return (
    <>
      <p className={styles.detail}>{resumoDePoliticas(acoes, efetivo)}</p>
      {categorias.map((g) => {
        const proprias = g.itens.filter((c) => origem(c).propria).map((c) => c.key);
        return (
          <details key={g.chave} open={g.chave === 'efeito'} className={styles.policyGroup}>
            <summary className={styles.disclosure}>
              <ChevronRight size={14} aria-hidden />
              {g.label}
              <Badge size="sm">{g.itens.length}</Badge>
              {proprias.length ? <Badge size="sm" tone="accent">{proprias.length} alterada(s) aqui</Badge> : null}
            </summary>
            <div className={styles.policyGroupBulk}>
              <span className={styles.detail}>tudo nesta categoria:</span>
              {POLICY_SEGMENT_OPTIONS.map((opt) => (
                <Button key={opt.value} size="sm" variant="ghost" disabled={salvando}
                        onClick={() => onChange(g.itens.map((c) => c.key), opt.value)}>
                  {opt.label}
                </Button>
              ))}
              {proprias.length ? (
                <Button size="sm" variant="ghost" icon={Undo2} disabled={salvando} onClick={() => onChange(proprias, null)}>
                  herdar tudo
                </Button>
              ) : null}
            </div>
            <ul className={styles.list}>
              {g.itens.map((c) => {
                const atual = efetivo(c);
                const o = origem(c);
                return (
                  <li key={c.key} className={styles.policyRow} data-policy-origin={o.propria ? 'propria' : 'herdada'}>
                    <span className={styles.policyRowTitle}>
                      {c.title}
                      <RiskBadge risk={c.risk} />
                      {c.side_effect ? <Badge tone="warning" size="sm">efeito externo</Badge> : null}
                      {loosened.includes(c.key) ? <Badge tone="danger" size="sm">mais frouxo que o padrão</Badge> : null}
                    </span>
                    <div className={styles.policyRowControl}>
                      <PolicySegmented value={atual} disabled={salvando} ariaLabel={`Política de ${c.title}`}
                                       onChange={(v) => onChange([c.key], v)} />
                      <Badge size="sm" tone={o.tone}>{o.rotulo}</Badge>
                      {o.propria ? (
                        <Button size="sm" variant="ghost" icon={Undo2} disabled={salvando}
                                title={`Apaga a escolha feita aqui; passa a valer ${POLICY_LABEL[herdaria(c)]} ${nomeDaHeranca(c)}`}
                                onClick={() => onChange([c.key], null)}>
                          herdar ({POLICY_LABEL[herdaria(c)]})
                        </Button>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          </details>
        );
      })}
    </>
  );
}

export function LimitsEditor({ limites, origem, herdaria, uso, salvando, onChange }: {
  limites: Record<string, number>;
  origem: (chave: string) => Origem;
  herdaria: (chave: string) => number | undefined;
  /** Uso de hoje por chave de limite (só no perfil; o grupo não tem uso próprio). */
  uso?: (chave: string) => number | null;
  salvando: boolean;
  onChange: (chave: string, valor: number | null) => void;
}) {
  return (
    <>
      {Object.entries(limites).map(([k, v]) => {
        const o = origem(k);
        const volta = herdaria(k);
        return (
          <LimitMeterCard key={k} label={LIMIT_LABEL[k] ?? k} usado={uso ? uso(k) : null} limite={v}>
            <Field label="Limite">
              {({ id }) => (
                <TextInput id={id} key={`${k}-${v}`} type="number" min={0} defaultValue={v} disabled={salvando}
                           onBlur={(e) => {
                             const n = Number(e.target.value);
                             if (Number.isFinite(n) && n >= 0 && n !== v) onChange(k, n);
                           }} />
              )}
            </Field>
            <div className={styles.limitOrigin}>
              <Badge size="sm" tone={o.tone}>{o.rotulo}</Badge>
              {o.propria ? (
                <Button size="sm" variant="ghost" icon={Undo2} disabled={salvando} onClick={() => onChange(k, null)}>
                  herdar{volta !== undefined ? ` (${volta})` : ''}
                </Button>
              ) : null}
            </div>
          </LimitMeterCard>
        );
      })}
    </>
  );
}
