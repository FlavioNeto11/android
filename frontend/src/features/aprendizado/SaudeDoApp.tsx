import { Flame, ShieldAlert } from 'lucide-react';
import { Badge } from '../../components/Badge';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { formatInt } from '../../lib/format';
import { LoadErrorBanner } from '../../lib/loadError';
import { useUiStore } from '../../store/ui';
import { apiAprendizado } from './api';
import { contarPorRotulo, falhasPorCapability, filaDeAtencao, motivoPrincipal, pedeAtencao, rotuloDaCapability } from './atencao';
import { hrefDoItem } from './DetalheRico';
import { metaDeSaude } from './detalhe';
import { LinhaDeFalha } from './FalhasTab';
import { chaveDoItem } from './ItemDoLivro';
import { ordenarFalhas, rotuloDoKind, type EntradaDoLivro } from './model';
import { useCarga } from './useCarga';
import styles from './Aprendizado.module.css';

/** A janela das falhas no detalhe do app: a mesma que a aba "O que mais falha" abre. */
const DIAS_DAS_FALHAS = 14;

/** A contagem por rótulo de saúde: o que o backend rotulou, sem recalcular nada (um chip por rótulo que existe). */
export function ChipsDeSaude({ itens }: { itens: readonly EntradaDoLivro[] }) {
  const contagem = contarPorRotulo(itens);
  if (contagem.length === 0) return <span>Nada medido ainda</span>;
  return (
    <span className={styles.chipsDeSaude} role="group" aria-label="Itens por saúde">
      {contagem.map(({ rotulo, n }) => {
        const meta = metaDeSaude(rotulo);
        return meta ? (
          <Badge key={rotulo} tone={meta.tone} size="sm" icon={meta.icon} title={meta.description} className={styles.seloDeSaude}>
            {formatInt(n)} {meta.label.toLowerCase()}
          </Badge>
        ) : null;
      })}
    </span>
  );
}

/**
 * A fila "Atenção" (§11.1): os itens que o backend rotulou `degradando`, `obsoleto_provavel` ou `sem_evidencia`, com o
 * motivo principal em português e o link para o item. Só leitura (D-5): nada é desligado nem avisado por estar aqui.
 * `nomes` (pacote → nome) só entra no Global, onde a fila mistura apps.
 */
export function FilaDeAtencao({ itens, nomes }: { itens: readonly EntradaDoLivro[]; nomes?: ReadonlyMap<string, string> }) {
  const fila = filaDeAtencao(itens);
  return (
    <section className={styles.secao} aria-label="Atenção">
      <h3 className={styles.secaoTitulo}>Atenção ({formatInt(fila.length)})</h3>
      <p className={styles.secaoLead}>
        O que o sistema mediu como degradando, provavelmente obsoleto ou sem evidência. É só leitura: nada é desligado nem
        avisado por estar aqui; a decisão continua sendo da pessoa, no item.
      </p>
      {fila.length === 0 ? (
        <EmptyState icon={ShieldAlert} compact title="Nada pede atenção agora" />
      ) : (
        <ul className={styles.lista} aria-label="Itens que pedem atenção">
          {fila.map((e) => {
            const meta = metaDeSaude(e.saude?.rotulo);
            const motivo = motivoPrincipal(e);
            const app = e.app ? (nomes?.get(e.app) ?? e.app) : null;
            return (
              <li key={chaveDoItem(e)} className={styles.item} data-atencao={chaveDoItem(e)}>
                <div className={styles.itemHead}>
                  {meta ? (
                    <Badge tone={meta.tone} size="sm" icon={meta.icon} title={meta.description} className={styles.seloDeSaude}>
                      <span className="sr-only">Saúde: </span>{meta.label}
                    </Badge>
                  ) : null}
                  <span className={styles.itemTitulo}>{rotuloDoKind(e.kind)} — {e.title}</span>
                  {nomes && app ? <Badge tone="neutral" size="sm" title="O aplicativo do item">{app}</Badge> : null}
                </div>
                <div className={styles.itemMeta}>
                  <span>{motivo ?? 'O backend não informou o motivo.'}</span>
                  <a className={styles.linkAlvo} href={hrefDoItem(e.kind, e.ref)}>Abrir o item</a>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/** Quantos itens do app pedem atenção, para o selo do cartão. */
export function quantosPedemAtencao(itens: readonly EntradaDoLivro[]): number {
  return itens.filter(pedeAtencao).length;
}

/**
 * "O que falha" neste app: os grupos do backlog (a mesma fonte da aba "O que mais falha", filtrada por pacote) agrupados
 * por capability. A ordem dentro de cada grupo é a do custo, vinda do backend.
 */
export function FalhasDoApp({ pacote }: { pacote: string }) {
  const { dado: rel, erro, carregar } = useCarga((s) => apiAprendizado.falhas({ dias: DIAS_DAS_FALHAS, app: pacote }, s), `falhas:${pacote}`);
  const grupos = falhasPorCapability(ordenarFalhas(rel?.grupos ?? []));
  return (
    <section className={styles.secao} aria-label="O que falha neste app">
      <h3 className={styles.secaoTitulo}>O que falha{rel ? ` (${formatInt(rel.grupos.length)})` : ''}</h3>
      <p className={styles.secaoLead}>
        Os grupos de falha deste app nos últimos {DIAS_DAS_FALHAS} dias, por capability (etapa livre = sem capability). Um grupo só
        entra com pelo menos 3 ocorrências; execuções simuladas ficam fora.
      </p>
      {erro && rel ? <LoadErrorBanner error={erro} onRetry={() => void carregar()} /> : null}
      {!rel ? (
        erro ? <LoadErrorBanner error={erro} onRetry={() => void carregar()} /> : (
          <LoadingRegion label="Agrupando as falhas…" className={styles.secao}><Skeleton height={64} radius={8} /></LoadingRegion>
        )
      ) : grupos.length === 0 ? (
        <EmptyState icon={Flame} compact title="Nenhum grupo de falha neste app" />
      ) : grupos.map((c) => (
        <div key={c.capability} className={styles.secao} role="group" aria-label={`Falhas de ${rotuloDaCapability(c.capability)}`}>
          <h4 className={styles.subtitulo}>{rotuloDaCapability(c.capability)} ({formatInt(c.itens.length)})</h4>
          <ol className={styles.lista} aria-label={`Falhas de ${rotuloDaCapability(c.capability)}`}>
            {c.itens.map((g, i) => <LinhaDeFalha key={g.id} g={g} posicao={i + 1} />)}
          </ol>
        </div>
      ))}
      <p className={styles.secaoLead}>
        <button type="button" className={styles.linkBtn}
                onClick={() => useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'falhas' } })}>
          Ver todas as falhas
        </button>
      </p>
    </section>
  );
}
