import { CheckCircle2, ExternalLink, TriangleAlert } from 'lucide-react';
import { useCallback, useEffect, useMemo } from 'react';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { Page } from '../../components/Page';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { cx } from '../../lib/format';
import { hashDe } from '../../lib/rotas';
import { tempoRelativo, useNow } from '../../lib/time';
import { PARAM_FOCO, useUiStore } from '../../store/ui';
import { nomeDe } from '../profiles/pessoa';
import { ROTULO_DA_ORIGEM, type OrigemDaPendencia, type Pendencia } from './modelo';
import { usePendenciasStore } from './store';
import { usePendencias } from './usePendencias';
import styles from './Pendencias.module.css';

const ORIGENS: readonly OrigemDaPendencia[] = ['aprendizado', 'persona', 'execucao', 'intervencao'];
const TOM: Record<OrigemDaPendencia, 'accent' | 'info' | 'warning' | 'danger'> = {
  aprendizado: 'accent', persona: 'info', execucao: 'warning', intervencao: 'danger',
};
const ehOrigem = (v: string | undefined): v is OrigemDaPendencia => !!v && (ORIGENS as readonly string[]).includes(v);

/**
 * Caixa única de pendências: tudo o que espera uma decisão sua, com a origem, há quanto tempo espera e um botão que
 * leva à tela onde se decide. O filtro por origem fica no link (`?origem=execucao`).
 */
export function PendenciasPage() {
  const agora = useNow();
  const origemDoLink = useUiStore((s) => s.rota.query.origem);
  const trocarQuery = useUiStore((s) => s.trocarQuery);
  // A mesma leitura de personas que a caixa já faz (para as sessões que pedem pessoa): uma chamada, não duas.
  const pessoas = usePendenciasStore((s) => s.personas);
  const nomeDaPersona = useCallback((id: string | null) => {
    const p = id ? pessoas?.find((x) => x.id === id) : null;
    return p ? nomeDe(p) : null;
  }, [pessoas]);
  const { itens, total, carregado, falhou } = usePendencias(nomeDaPersona);
  const filtro = ehOrigem(origemDoLink) ? origemDoLink : null;

  useEffect(() => {
    void usePendenciasStore.getState().atualizar();
  }, []);

  const contagem = useMemo(() => {
    const c: Record<OrigemDaPendencia, number> = { aprendizado: 0, persona: 0, execucao: 0, intervencao: 0 };
    for (const p of itens) c[p.origem] += 1;
    return c;
  }, [itens]);
  const visiveis = filtro ? itens.filter((p) => p.origem === filtro) : itens;

  return (
    <Page
      title="Pendências"
      lead="Tudo o que espera uma decisão sua, num lugar só: aprendizados para aprovar, textos das personas, execuções que pararam esperando você e contas que pedem uma intervenção (login, desafio de segurança, conta errada)."
    >
      {falhou ? (
        <Banner tone="warning" icon={TriangleAlert} compact role="status">
          Não foi possível ler todas as origens agora. A lista pode estar incompleta; ela se atualiza sozinha.
        </Banner>
      ) : null}

      <div className={styles.chips} role="radiogroup" aria-label="Filtrar por origem">
        <button type="button" role="radio" aria-checked={!filtro} className={cx(styles.chip, !filtro && styles.chipOn)}
                onClick={() => trocarQuery({ origem: undefined })}>
          Todas ({total})
        </button>
        {ORIGENS.map((o) => (
          <button key={o} type="button" role="radio" aria-checked={filtro === o}
                  className={cx(styles.chip, filtro === o && styles.chipOn)} onClick={() => trocarQuery({ origem: o })}>
            {ROTULO_DA_ORIGEM[o]} ({contagem[o]})
          </button>
        ))}
      </div>

      {!carregado && itens.length === 0 ? (
        <LoadingRegion label="Carregando as pendências…"><Skeleton height={64} radius={8} /><Skeleton height={64} radius={8} /></LoadingRegion>
      ) : visiveis.length === 0 ? (
        <EmptyState icon={CheckCircle2} title={filtro ? `Nada de ${ROTULO_DA_ORIGEM[filtro].toLowerCase()} esperando você` : 'Nada esperando você'}
                    hint={filtro ? 'Escolha "Todas" para ver as outras origens.' : 'Quando algo precisar da sua decisão, aparece aqui e no contador do menu.'}>
          Nenhuma pendência.
        </EmptyState>
      ) : (
        <ul className={styles.lista} aria-label="Pendências">
          {visiveis.map((p) => <Linha key={p.chave} p={p} agora={agora} />)}
        </ul>
      )}

      <Disclosure summary="Como o número é contado" bare>
        <p className={styles.nota}>
          Cada linha é uma decisão sua: um aprendizado, um texto de persona, uma execução ou uma conta que pede
          intervenção (a mesma fila &quot;Aguardando intervenção&quot; de Personas). O número no menu é o total
          desta lista. O &quot;aguardando você&quot; do topo conta os <em>objetivos</em> dentro das execuções, por isso pode
          ser maior que o número de execuções listadas aqui.
        </p>
      </Disclosure>
    </Page>
  );
}

function Linha({ p, agora }: { p: Pendencia; agora: number }) {
  // O aparelho em Foco vai junto, como nos links do menu: abrir a decisão não fecha o painel aberto.
  const foco = useUiStore((s) => s.focusInstanceId);
  const href = hashDe(p.destino.tela, { segmentos: p.destino.segmentos, query: { ...p.destino.query, [PARAM_FOCO]: foco ?? undefined } });
  return (
    <li className={styles.linha} data-origem={p.origem}>
      <div className={styles.corpo}>
        <div className={styles.topo}>
          <Badge size="sm" tone={TOM[p.origem]}>{ROTULO_DA_ORIGEM[p.origem]}</Badge>
          <span className={styles.idade} title={p.desde ?? undefined}>
            {p.desde ? `esperando ${tempoRelativo(p.desde, agora)}` : 'há quanto tempo: não informado'}
          </span>
        </div>
        <strong className={styles.titulo} title={p.titulo}>{p.titulo}</strong>
        <span className={styles.detalhe}>{p.detalhe}</span>
      </div>
      <a className={styles.acao} href={href} aria-label={`${p.acao}: ${p.titulo}`}>
        {p.acao} <ExternalLink size={14} aria-hidden />
      </a>
    </li>
  );
}
