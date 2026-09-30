import { Sparkles } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { ProfileCapabilities } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { EmptyState } from '../../components/EmptyState';
import { ProgressBar } from '../../components/ProgressBar';
import { StatusBadge } from '../../components/StatusBadge';
import { FLOW_STATUS, metaOf } from '../../lib/status';
import { formatAgo, useNow } from '../../lib/time';
import { toastError } from '../../store/toasts';
import { Carregando, Linha, useVersaoAoVivo } from './detalheComum';
import type { Pessoa } from './pessoa';
import styles from './Profiles.module.css';

// ---------------------------------------------------------------- habilidades: o que a persona já fez e o que roda sem IA
const CUSTO_IA: Record<string, { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' }> = {
  zero: { label: 'roda sem IA', tone: 'success' },
  parcial: { label: 'parte por receita', tone: 'warning' },
  total: { label: 'a IA faz tudo', tone: 'danger' },
  desconhecido: { label: 'cobertura desconhecida', tone: 'neutral' },
};

export function AbaHabilidades({ profile }: { profile: Pessoa }) {
  const [dados, setDados] = useState<ProfileCapabilities | null>(null);
  const now = useNow();
  const versao = useVersaoAoVivo(profile);
  useEffect(() => {
    let vivo = true;
    api.profileCapabilities(profile.id)
      .then((d) => { if (vivo) setDados(d); })
      .catch((e) => { toastError('Não foi possível carregar', e); if (vivo) setDados({ profile_id: profile.id, flows: [], steps_driven_by: {}, recipe_share: null, interactions: {} }); });
    return () => { vivo = false; };
  }, [profile.id, versao]);
  if (dados === null) return <Carregando />;
  const totalEtapas = Object.values(dados.steps_driven_by).reduce((a, b) => a + b, 0);
  const interacoes = Object.entries(dados.interactions);
  const treinadas = dados.trained ?? [];
  const cartaoTreinadas = treinadas.length ? (
    <Card>
      <CardHeader title="Ensinadas por você" subtitle="Habilidades do modo treinamento que valem para esta persona. Peça pelo comando." />
      <CardBody>
        <ul className={styles.list}>
          {treinadas.map((f) => (
            <li key={f.flow_id} className={styles.policyRow}>
              <span className={styles.policyRowTitle}>{f.name}<Badge size="sm" tone="accent">treinada</Badge>
                <StatusBadge meta={metaOf(FLOW_STATUS, f.status)} size="sm" /></span>
              <code className={styles.detail}>{f.command_template}</code>
              <span className={styles.muted}>{f.uses}× · {f.scope}</span>
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  ) : null;
  if (dados.flows.length === 0 && totalEtapas === 0 && interacoes.length === 0) {
    if (cartaoTreinadas) return <div className={styles.grid}>{cartaoTreinadas}</div>;
    return (
      <EmptyState icon={Sparkles} title="Nada mapeado ainda" hint="Cada execução concluída vira um fluxo; cada etapa que a IA resolveu vira receita. Aqui aparece o que esta persona já sabe fazer.">
        Esta persona ainda não concluiu nenhuma execução.
      </EmptyState>
    );
  }
  const maxInteracao = Math.max(1, ...interacoes.map(([, n]) => n));
  const pct = dados.recipe_share === null ? null : Math.round(dados.recipe_share * 100);
  return (
    <div className={styles.grid}>
      {cartaoTreinadas}
      <Card>
        <CardHeader title="Caminhos que esta persona já percorreu"
                    subtitle="Cada bolinha é uma etapa: verde tem receita própria; a cor de quem falta muda com o quanto o fluxo ainda depende da IA." />
        <CardBody>
          {dados.flows.length === 0 ? <p className={styles.muted}>Nenhum fluxo concluído.</p> : (
            <div>
              {dados.flows.map((f) => {
                const custo = CUSTO_IA[f.ai_cost] ?? { label: 'cobertura desconhecida', tone: 'neutral' as const };
                const restoTone: 'success' | 'warning' | 'danger' = f.ai_cost === 'total' ? 'danger' : f.ai_cost === 'zero' ? 'success' : 'warning';
                return (
                  <div key={f.flow_id} className={styles.skillTrail}>
                    <div className={styles.skillTrailHead}>
                      <strong>{f.name}</strong>
                      <Badge tone={custo.tone}>{custo.label}</Badge>
                      <span className={styles.muted}>
                        {f.target_version ? `versão ${f.target_version} · ` : ''}{f.times ?? 0}× · último: {f.last_at ? formatAgo(f.last_at, now) : '—'}
                      </span>
                    </div>
                    <div className={styles.skillDots} role="img"
                         aria-label={`${f.steps_with_recipe} de ${f.steps_total} etapas com receita`}>
                      {Array.from({ length: f.steps_total }, (_, i) => (
                        <span key={i} className={styles.skillDot} data-tone={i < f.steps_with_recipe ? 'success' : restoTone} />
                      ))}
                    </div>
                    <div className={styles.detail}>{f.command_template}</div>
                  </div>
                );
              })}
            </div>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Fração que roda sem IA" />
        <CardBody style={{ display: 'flex', gap: 'var(--sp-4)', alignItems: 'center' }}>
          <div className={styles.ring} style={{ background: `conic-gradient(var(--accent) ${pct ?? 0}%, var(--surface-4) 0)` }}>
            <div className={styles.ringInner}>{pct === null ? '—' : `${pct}%`}</div>
          </div>
          <dl className={styles.rows} style={{ flex: 1 }}>
            <Linha rotulo="Por receita (sem IA)">{dados.steps_driven_by.recipe ?? 0}</Linha>
            <Linha rotulo="Receita + IA">{dados.steps_driven_by['recipe+ai'] ?? 0}</Linha>
            <Linha rotulo="Só IA">{dados.steps_driven_by.ai ?? 0}</Linha>
          </dl>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Interações confirmadas, por tipo" />
        <CardBody className={styles.form}>
          {interacoes.length === 0 ? <p className={styles.muted}>Nenhuma.</p> : (
            interacoes.map(([tipo, n]) => (
              <ProgressBar key={tipo} value={n / maxInteracao} label={tipo} text={`${tipo}: ${n}`} tone="accent" />
            ))
          )}
        </CardBody>
      </Card>
    </div>
  );
}
