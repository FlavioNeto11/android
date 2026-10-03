import { History } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { LoadErrorBanner, LoadErrorState, toLoadError, type LoadError } from '../../lib/loadError';
import { formatDateTime } from '../../lib/time';
import { apiAprendizado } from './api';
import { formatUsd, hrefDoItemDaRevisao, type RevisaoDoCurador } from './metricas';
import { AppsDoItem, eMultiApp } from './AppsDoItem';
import { rotuloDoKind, tituloDoItem, type LivroKind } from './model';
import {
  ladoDaDecisao, textoDaConfianca, textoDaDecisao, textoDaDecisaoFinal, textoDaValidade, textoDoGatilho,
  textoDoResultadoPosterior, type DecisaoDaIA,
} from './parecer';
import styles from './Aprendizado.module.css';

const POR_PAGINA = 20;
const DECISOES: readonly DecisaoDaIA[] = ['aprovar', 'manter', 'observar', 'pedir_evidencia', 'rebaixar', 'desativar',
                                          'substituir', 'fundir', 'possivelmente_obsoleto'];
const TOM_DO_LADO = { sobe: 'success', desce: 'danger', espera: 'info' } as const;

function LinhaDaRevisao({ r }: { r: RevisaoDoCurador }) {
  const href = hrefDoItemDaRevisao(r);
  const kind = r.item_ref.includes(':') ? r.item_ref.slice(0, r.item_ref.indexOf(':')) : r.item_kind;
  const ref = r.item_ref.includes(':') ? r.item_ref.slice(r.item_ref.indexOf(':') + 1) : r.item_ref;
  // O título do item como no catálogo (validação do deploy 10: o fluxo saía pelo id cortado); a referência fica no
  // `title`, e é ela que aparece quando o item já saiu do livro.
  const referencia = `${rotuloDoKind(kind)} ${kind === 'receita' ? `nº ${ref}` : ref}`;
  const nome = r.titulo
    ? `${rotuloDoKind(kind)}: ${tituloDoItem({ kind: kind as LivroKind, ref, title: r.titulo, capability: r.capability,
                                              capability_nome: r.capability_nome, etapa: r.etapa })}`
    : referencia;
  const semParecer = textoDaValidade(r.validade);
  const final = textoDaDecisaoFinal({ ...r, override_motivo: null });
  const desfecho = textoDoResultadoPosterior(r.resultado_posterior);
  return (
    <li className={styles.item} data-revisao={r.id}>
      <div className={styles.itemHead}>
        <span className={styles.itemTitulo}>
          {/* O corte em duas linhas fica no texto: o link é inline-flex (área de clique) e anularia o do título. */}
          {href
            ? <a className={styles.linkAlvo} href={href} title={referencia}><span className={styles.tituloDaRevisao}>{nome}</span></a>
            : <span className={styles.tituloDaRevisao} title={referencia}>{nome}</span>}
        </span>
        {r.decisao ? (
          <Badge tone={TOM_DO_LADO[ladoDaDecisao(r.decisao)]} size="sm" title="O que a IA sugeriu">{textoDaDecisao(r.decisao)}</Badge>
        ) : null}
        {r.classe ? <Badge tone="neutral" size="sm" title="Classe de risco do item">Classe {r.classe}</Badge> : null}
        {r.simulado ? <Badge tone="muted" size="sm" title="Provedor simulado: fica só como registro">simulado</Badge> : null}
      </div>
      <div className={styles.itemMeta}>
        <span>{formatDateTime(r.criado_em)}</span>
        {eMultiApp(r.apps) ? <AppsDoItem apps={r.apps} nomes={r.apps_nomes} principal={r.app} />
          : r.app ? <span title={r.app}>{r.app_nome ?? r.app}</span> : null}
        {r.gatilho ? <span>por {textoDoGatilho(r.gatilho)}</span> : null}
        {r.decisao ? <span>{textoDaConfianca(r.confianca)}</span> : null}
        <span title={[r.provedor, r.modelo].filter(Boolean).join(' · ') || undefined}>{formatUsd(r.usd)}</span>
      </div>
      {semParecer ? <p className={styles.notaDoItem}>{semParecer}</p> : null}
      {final ? <p className={styles.notaDoItem}>{final}</p> : null}
      {/* 30.35: o que aconteceu com o item 14 dias depois (o rótulo 2 do golden set), agora também na tela. */}
      {desfecho ? <p className={styles.notaDoItem} title={r.resultado_em ? `Medido em ${formatDateTime(r.resultado_em)}` : undefined}>Em 14 dias, o item {desfecho}.</p> : null}
    </li>
  );
}

/**
 * Os pareceres do curador (`GET /api/aprendizado/revisoes`), do mais novo ao mais velho, em páginas pelo cursor. Cada
 * linha leva ao item no catálogo, onde está o parecer inteiro e o dossiê.
 */
export function RevisoesDoCurador({ app }: { app: string }) {
  const [decisao, setDecisao] = useState('');
  const [linhas, setLinhas] = useState<RevisaoDoCurador[] | null>(null);
  const [proximo, setProximo] = useState<string | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [carregando, setCarregando] = useState(true);
  const vez = useRef(0);

  const buscar = useCallback(async (cursor: string | null) => {
    const minha = ++vez.current;
    setCarregando(true);
    try {
      const p = await apiAprendizado.revisoes({ app, decisao, limite: POR_PAGINA, cursor });
      if (minha !== vez.current) return;
      setLinhas((antes) => (cursor && antes ? [...antes, ...p.revisoes] : p.revisoes));
      setProximo(p.proximo);
      setErro(null);
    } catch (e) {
      if (minha === vez.current) setErro(toLoadError(e));
    } finally {
      if (minha === vez.current) setCarregando(false);
    }
  }, [app, decisao]);

  useEffect(() => {
    setLinhas(null);
    setProximo(null);
    void buscar(null);
  }, [buscar]);

  return (
    <section className={styles.secao} aria-labelledby="revisoes-do-curador">
      <div className={styles.toolbar}>
        <h3 id="revisoes-do-curador" className={styles.secaoTitulo}><History size={18} aria-hidden /> Pareceres do curador</h3>
        <div className={styles.toolbarFim}>
          <Field label="Sugestão da IA" className={styles.filtro}>
            {({ id }) => (
              <Select id={id} small value={decisao} onChange={(e) => setDecisao(e.target.value)}>
                <option value="">Todas</option>
                {DECISOES.map((d) => <option key={d} value={d}>{textoDaDecisao(d)}</option>)}
              </Select>
            )}
          </Field>
        </div>
      </div>
      <p className={styles.secaoLead}>Todas as janelas, do mais novo ao mais velho. Abra o item para ver o parecer inteiro e decidir.</p>

      {erro && linhas ? <LoadErrorBanner error={erro} onRetry={() => void buscar(linhas.length > 0 ? proximo : null)} /> : null}
      {!linhas ? (
        erro ? <LoadErrorState what="os pareceres do curador" error={erro} onRetry={() => void buscar(null)} /> : (
          <LoadingRegion label="Lendo os pareceres…" className={styles.secao}>
            <Skeleton height={64} radius={8} />
            <Skeleton height={64} radius={8} />
          </LoadingRegion>
        )
      ) : linhas.length === 0 ? (
        <EmptyState icon={History} compact title={decisao ? 'Nenhum parecer com esta sugestão' : 'Nenhum parecer do curador ainda'}>
          {decisao ? 'Troque o filtro para ver os outros.' : 'O curador revisa itens do livro quando está ligado ou em sombra.'}
        </EmptyState>
      ) : (
        <>
          <ul className={styles.lista} aria-label="Pareceres do curador">
            {linhas.map((r) => <LinhaDaRevisao key={r.id} r={r} />)}
          </ul>
          {proximo ? (
            <div className={styles.itemAcoes}>
              <Button size="sm" variant="outline" loading={carregando} onClick={() => void buscar(proximo)}>Carregar mais</Button>
            </div>
          ) : <p className={styles.cartaoUso}>Fim da lista.</p>}
        </>
      )}
    </section>
  );
}
